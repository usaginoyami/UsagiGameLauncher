import hashlib
import io
import os
import stat
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usagi_launcher import catalog, installer, paths  # noqa: E402
from usagi_launcher.errors import Cancelled, InstallError, RateLimitError  # noqa: E402
from usagi_launcher.github_api import Asset, GitHubClient, Release, pick_release  # noqa: E402
from usagi_launcher.library import Library  # noqa: E402
from usagi_launcher.settings import Settings  # noqa: E402


def A(name, size=1000, digest=""):
    return Asset(name=name, size=size, url="https://example.invalid/" + name, digest=digest)


class AssetPicking(unittest.TestCase):
    def test_prefers_windows_zip(self):
        assets = [A("Game-Linux-X64.zip"), A("Game-Windows.zip"), A("Game-macOS.dmg"),
                  A("Game-Source.zip"), A("Game-Windows-arm64.zip")]
        self.assertEqual(installer.pick_asset(assets).name, "Game-Windows.zip")

    def test_no_windows_package(self):
        self.assertIsNone(installer.pick_asset([A("a-linux.tar.gz"), A("b.AppImage"), A("notes.txt")]))

    def test_unlabeled_zip_is_acceptable(self):
        self.assertEqual(installer.pick_asset([A("Zelda64Recompiled.zip")]).name, "Zelda64Recompiled.zip")

    def test_hint_wins(self):
        assets = [A("Game-Windows.zip"), A("Game-Windows-Portable.zip")]
        self.assertEqual(installer.pick_asset(assets, "portable").name, "Game-Windows-Portable.zip")

    def test_darwin_is_not_windows(self):
        self.assertIsNone(installer.pick_asset([A("game-darwin.zip")]))


class ReleasePicking(unittest.TestCase):
    def test_stable_over_prerelease(self):
        data = [{"tag_name": "v2-beta", "prerelease": True}, {"tag_name": "v1", "prerelease": False}]
        self.assertEqual(pick_release(data).tag, "v1")

    def test_falls_back_to_prerelease(self):
        data = [{"tag_name": "v2-beta", "prerelease": True}, {"tag_name": "d", "draft": True}]
        self.assertEqual(pick_release(data, True).tag, "v2-beta")
        self.assertIsNone(pick_release(data, False))

    def test_empty(self):
        self.assertIsNone(pick_release([]))


class Extraction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _zip(self, members: dict[str, bytes]) -> Path:
        p = self.tmp / "a.zip"
        with zipfile.ZipFile(p, "w") as z:
            for n, data in members.items():
                z.writestr(n, data)
        return p

    def test_extracts(self):
        z = self._zip({"Game/Game.exe": b"MZ", "Game/data/x.bin": b"1"})
        out = self.tmp / "out"
        installer.safe_extract_zip(z, out)
        self.assertTrue((out / "Game" / "Game.exe").is_file())

    def test_zip_slip_blocked(self):
        z = self._zip({"../evil.txt": b"x"})
        with self.assertRaises(InstallError):
            installer.safe_extract_zip(z, self.tmp / "out2")
        self.assertFalse((self.tmp / "evil.txt").exists())

    def test_absolute_path_blocked(self):
        z = self._zip({"/etc/evil": b"x"})
        with self.assertRaises(InstallError):
            installer.safe_extract_zip(z, self.tmp / "out3")

    def test_cancel(self):
        z = self._zip({"a.bin": b"x" * 10})
        ev = threading.Event()
        ev.set()
        with self.assertRaises(Cancelled):
            installer.safe_extract_zip(z, self.tmp / "out4", cancel=ev)

    def test_find_exe_ignores_helpers(self):
        d = self.tmp / "g"
        (d / "bin").mkdir(parents=True)
        (d / "unins000.exe").write_bytes(b"x" * 5000)
        (d / "vc_redist.x64.exe").write_bytes(b"x" * 9000)
        (d / "bin" / "Banjo.exe").write_bytes(b"x")
        (d / "Launcher.exe").write_bytes(b"x")
        found = installer.find_executables(d, "Banjo Recomp")
        self.assertEqual(found[0].name, "Banjo.exe")
        self.assertNotIn("unins000.exe", [p.name for p in found])

    def test_unique_dir(self):
        d = self.tmp / "v1"
        d.mkdir()
        (d / "f").write_text("x")
        self.assertEqual(installer.unique_dir(d).name, "v1_2")


class FakeClient(GitHubClient):
    """Cliente que serve um zip em vez de baixar da rede."""

    def __init__(self, payload: bytes):
        super().__init__("")
        self.payload = payload

    def download(self, url, dest, progress=None, cancel=None, expected_size=0):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.payload)
        if progress:
            progress(len(self.payload), len(self.payload))
        return Path(dest)


class InstallFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Game/Game.exe", b"MZ")
        self.payload = buf.getvalue()
        self.rel = Release("v1.0.0", "v1", "2026-01-01T00:00:00Z", False, "u", "")

    def _asset(self, digest=""):
        return A("Game-Windows.zip", len(self.payload), digest)

    def test_install_with_valid_digest(self):
        d = "sha256:" + hashlib.sha256(self.payload).hexdigest()
        res = installer.install(FakeClient(self.payload), self.rel, self._asset(d), "My: Game?",
                                self.tmp / "games", self.tmp / "dl")
        self.assertEqual(res.exe.name, "Game.exe")
        self.assertEqual(res.folder.name, "v1.0.0")
        self.assertEqual(res.root.name, "My_ Game_")
        self.assertFalse(any((self.tmp / "dl").iterdir()))  # zip removido

    def test_bad_digest_aborts_and_cleans(self):
        with self.assertRaises(InstallError):
            installer.install(FakeClient(self.payload), self.rel, self._asset("sha256:" + "0" * 64),
                              "Game", self.tmp / "games", self.tmp / "dl")
        self.assertFalse((self.tmp / "games" / "Game" / "v1.0.0").exists())
        self.assertFalse(any((self.tmp / "dl").iterdir()))


class CatalogTests(unittest.TestCase):
    def test_parse_repo(self):
        for text, want in [("Mr-Wiseguy/Zelda64Recomp", "Mr-Wiseguy/Zelda64Recomp"),
                           ("https://github.com/BanjoRecomp/BanjoRecomp/releases", "BanjoRecomp/BanjoRecomp"),
                           ("github.com/a/b.git", "a/b"),
                           ("git@github.com:a/b.git", "a/b"),
                           ("só-texto", None), ("", None), ("a/", None)]:
            self.assertEqual(catalog.parse_repo(text), want, text)

    def test_platform_guess(self):
        self.assertEqual(catalog.guess_platform("Recompilation of an N64 game"), "N64")
        self.assertEqual(catalog.guess_platform("Xbox 360 static recompilation"), "Xbox 360")
        self.assertEqual(catalog.guess_platform("Wii port"), "Wii")
        self.assertEqual(catalog.guess_platform("algo"), "Outros")

    def test_tool_filter(self):
        self.assertTrue(catalog.is_tool_repo("N64Recomp", "Tool to statically recompile N64 games"))
        self.assertTrue(catalog.is_tool_repo("XenonRecomp", ""))
        self.assertFalse(catalog.is_tool_repo("BanjoRecomp", "Banjo-Kazooie recompiled for PC"))

    def test_bundled_catalog_is_valid(self):
        games = catalog.load_bundled()
        self.assertGreaterEqual(len(games), 10)
        self.assertEqual(len({g.key for g in games}), len(games))
        for g in games:
            self.assertEqual(catalog.parse_repo(g.repo), g.repo)

    def test_merge_priority_and_hidden(self):
        u = [catalog.Entry("a/b", name="Meu", source="user")]
        b = [catalog.Entry("A/B", name="Cat", source="catalog"), catalog.Entry("c/d", source="catalog")]
        d = [catalog.Entry("a/b", source="discovered"), catalog.Entry("x/y", source="discovered"),
             catalog.Entry("z/w", source="discovered")]
        out = {e.key: e for e in catalog.merge(u, b, d, hidden={"z/w"})}
        self.assertEqual(set(out), {"a/b", "c/d", "x/y"})
        self.assertEqual(out["a/b"].name, "Meu")

    def test_discover_filters(self):
        class C:
            def search_repos(self, topic, stars):
                return [
                    {"full_name": "o/GameRecomp", "name": "GameRecomp", "description": "Mario 64 N64 recomp",
                     "stargazers_count": 50, "topics": ["recomp"]},
                    {"full_name": "o/N64Recomp", "name": "N64Recomp", "description": "Tool", "stargazers_count": 900},
                    {"full_name": "o/old", "name": "old", "archived": True},
                    {"full_name": "o/fork", "name": "fork", "fork": True},
                    {"full_name": "o/GameRecomp", "name": "GameRecomp"},
                ]
        items, err = catalog.discover(C(), ["recomp", "recompiled"], 5)
        self.assertIsNone(err)
        self.assertEqual([e.repo for e in items], ["o/GameRecomp"])
        self.assertEqual(items[0].platform, "N64")

    def test_discover_rate_limit_keeps_partial(self):
        class C:
            n = 0
            def search_repos(self, topic, stars):
                C.n += 1
                if C.n == 2:
                    raise RateLimitError(0)
                return [{"full_name": "o/AGame", "name": "AGame", "description": "recomp", "stargazers_count": 1}]
        items, err = catalog.discover(C(), ["a", "b", "c"], 0)
        self.assertEqual(len(items), 1)
        self.assertIsInstance(err, RateLimitError)


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        paths.set_data_dir(self.tmp)

    def tearDown(self):
        paths.set_data_dir(None)

    def test_library_roundtrip_and_versions(self):
        lib = Library(self.tmp / "lib.json")
        lib.record_install("a/B", "B", "v1", "/g/B/v1", "/g/B/v1/b.exe", "/g/B")
        lib.record_install("a/b", "B", "v2", "/g/B/v2", "/g/B/v2/b.exe", "/g/B")
        again = Library(self.tmp / "lib.json")
        rec = again.get("A/B")
        self.assertEqual(rec["version"], "v2")
        self.assertEqual(len(rec["versions"]), 2)
        self.assertEqual(again.drop_old_versions("a/b"), ["/g/B/v1"])
        self.assertEqual(len(again.get("a/b")["versions"]), 1)

    def test_corrupt_file_falls_back_to_backup(self):
        lib = Library(self.tmp / "lib.json")
        lib.record_install("a/b", "B", "v1", "/f", None, "/r")
        lib.record_install("a/b", "B", "v2", "/f2", None, "/r")  # cria .bak com v1
        (self.tmp / "lib.json").write_text("{corrompido", encoding="utf-8")
        self.assertEqual(Library(self.tmp / "lib.json").get("a/b")["version"], "v1")

    def test_settings_lists(self):
        s = Settings(self.tmp / "s.json")
        self.assertTrue(s.toggle("favorites", "a/b"))
        self.assertFalse(s.toggle("favorites", "a/b"))
        s.add_to("hidden", "x/y")
        s.add_to("hidden", "x/y")
        s.save()
        s2 = Settings(self.tmp / "s.json")
        self.assertEqual(s2.get("hidden"), ["x/y"])
        self.assertTrue(s2.get("include_prereleases"))

    def test_user_catalog_roundtrip(self):
        catalog.save_user([catalog.Entry("o/r", name="X", platform="N64")])
        self.assertEqual(catalog.load_user()[0].name, "X")


class FakeResp:
    def __init__(self, status=200, data=None, headers=None, text=""):
        self.status_code, self._data, self.headers, self.text = status, data, headers or {}, text
        self.ok = 200 <= status < 300

    def json(self):
        return self._data


class ApiCache(unittest.TestCase):
    def test_etag_304_reuses_cache(self):
        sess = mock.Mock()
        sess.headers = {}
        sess.get.side_effect = [
            FakeResp(200, [{"tag_name": "v1"}], {"ETag": 'W/"1"', "X-RateLimit-Remaining": "50"}),
            FakeResp(304, None, {"X-RateLimit-Remaining": "50"}),
        ]
        c = GitHubClient("tok", session=sess)
        self.assertEqual(c.latest_release("o/r").tag, "v1")
        self.assertEqual(c.latest_release("o/r").tag, "v1")
        self.assertEqual(sess.get.call_args_list[1].kwargs["headers"]["If-None-Match"], 'W/"1"')
        self.assertEqual(c.rate_remaining, 50)
        self.assertEqual(sess.headers["Authorization"], "Bearer tok")

    def test_rate_limit_without_cache_raises(self):
        sess = mock.Mock()
        sess.headers = {}
        sess.get.return_value = FakeResp(403, None, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "99"})
        with self.assertRaises(RateLimitError):
            GitHubClient("", session=sess).repo_info("o/r")

    def test_rate_limit_with_cache_serves_stale(self):
        sess = mock.Mock()
        sess.headers = {}
        sess.get.side_effect = [FakeResp(200, {"a": 1}, {"ETag": "x"}),
                                FakeResp(403, None, {"X-RateLimit-Remaining": "0"})]
        c = GitHubClient("", session=sess)
        c.repo_info("o/r")
        self.assertEqual(c.repo_info("o/r"), {"a": 1})


class Launch(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "script de shell")
    def test_launch_runs_in_game_folder(self):
        from usagi_launcher import launcher
        tmp = Path(tempfile.mkdtemp())
        script = tmp / "run.sh"
        script.write_text("#!/bin/sh\npwd > out.txt\necho \"$1\" >> out.txt\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        proc = launcher.launch(script, "--play")
        proc.wait(5)
        lines = (tmp / "out.txt").read_text().split()
        self.assertEqual(Path(lines[0]).resolve(), tmp.resolve())
        self.assertEqual(lines[1], "--play")


if __name__ == "__main__":
    unittest.main(verbosity=2)
