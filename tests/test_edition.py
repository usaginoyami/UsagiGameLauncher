"""Edições, catálogo publicado, segredos, tempo de jogo, atalhos, versões e reparo."""
import base64
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usagi_launcher import (edition, installer, paths, published, selfupdate, shortcuts,  # noqa: E402
                            sorting, tracking, vault)
from usagi_launcher.catalog import Entry  # noqa: E402
from usagi_launcher.errors import InstallError  # noqa: E402
from usagi_launcher.github_api import Asset, Release  # noqa: E402
from usagi_launcher.library import Library  # noqa: E402
from usagi_launcher.settings import SECRET_KEYS, Settings  # noqa: E402


class Resp:
    def __init__(self, status=200, body=None, headers=None, content=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = content if content is not None else json.dumps(body).encode()
        self.ok = status < 400

    def json(self):
        if self._body is None:
            raise ValueError("sem json")
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def put(self, url, **kw):
        self.calls.append(("PUT", url, kw))
        return self.responses.pop(0)


# ------------------------------------------------------------------ edição
class Edition(unittest.TestCase):
    def tearDown(self):
        edition.set_edition(None)

    def test_default_is_curator(self):
        edition.set_edition(None)
        self.assertEqual(edition.current(), edition.CURATOR)

    def test_client_names_and_assets(self):
        edition.set_edition(edition.CLIENT)
        self.assertTrue(edition.is_client())
        self.assertEqual(edition.exe_name(), "UsagiGameClient.exe")
        self.assertTrue(edition.asset_matches("UsagiGameLauncher-Client-Windows-x64.zip"))
        self.assertFalse(edition.asset_matches("UsagiGameLauncher-Windows-x64.zip"))
        edition.set_edition(edition.CURATOR)
        self.assertTrue(edition.asset_matches("UsagiGameLauncher-Windows-x64.zip"))
        self.assertFalse(edition.asset_matches("UsagiGameLauncher-Client-Windows-x64.zip"))

    def test_data_folder_differs(self):
        edition.set_edition(edition.CLIENT)
        a = edition.data_folder()
        edition.set_edition(edition.CURATOR)
        self.assertNotEqual(a, edition.data_folder())

    def test_self_update_picks_own_package(self):
        zips = [Asset("UsagiGameLauncher-Windows-x64.zip", 1, "u1"),
                Asset("UsagiGameLauncher-Client-Windows-x64.zip", 1, "u2")]
        rel = Release("v9.0.0", "v9", "2026-10-01T00:00:00Z", False, "", "", zips)

        class C:
            def latest_release(self, *a, **k):
                return rel

        edition.set_edition(edition.CLIENT)
        self.assertIn("Client", selfupdate.find_update(C())[1].name)
        edition.set_edition(edition.CURATOR)
        self.assertNotIn("Client", selfupdate.find_update(C())[1].name)


# ------------------------------------------------------------------ cofre
class Secrets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "settings.json"

    def test_secrets_leave_the_file(self):
        v = vault.MemoryBackend()
        s = Settings(self.path, backend=v)
        s.set("github_token", "ghp_secret")
        s.set("steamgriddb_key", "k")
        s.set("install_dir", "D:/Jogos")
        s.save()
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw["github_token"], "")
        self.assertNotIn("ghp_secret", self.path.read_text())
        self.assertEqual(raw["install_dir"], "D:/Jogos")
        again = Settings(self.path, backend=v)
        self.assertEqual(again.get("github_token"), "ghp_secret")
        self.assertEqual(again.get("steamgriddb_key"), "k")

    def test_plaintext_is_migrated(self):
        self.path.write_text(json.dumps({"github_token": "old", "igdb_client_secret": "s"}))
        v = vault.MemoryBackend()
        s = Settings(self.path, backend=v)
        self.assertEqual(s.get("github_token"), "old")
        s.save()
        self.assertEqual(v.get("github_token"), "old")
        self.assertEqual(json.loads(self.path.read_text())["github_token"], "")

    def test_no_vault_keeps_file(self):
        s = Settings(self.path, backend=None)
        s.set("github_token", "x")
        s.save()
        self.assertEqual(json.loads(self.path.read_text())["github_token"], "x")
        self.assertFalse(s.secrets_protected())

    def test_failing_vault_falls_back_to_file(self):
        class Broken(vault.MemoryBackend):
            def set(self, name, value):
                return False

        s = Settings(self.path, backend=Broken())
        s.set("github_token", "x")
        s.save()
        self.assertEqual(json.loads(self.path.read_text())["github_token"], "x")

    def test_clearing_deletes_from_vault(self):
        v = vault.MemoryBackend()
        s = Settings(self.path, backend=v)
        s.set("github_token", "x")
        s.save()
        s.set("github_token", "")
        s.save()
        self.assertIsNone(v.get("github_token"))
        self.assertEqual(Settings(self.path, backend=v).get("github_token"), "")

    def test_all_secret_keys_are_known_defaults(self):
        s = Settings(self.path, backend=None)
        for k in SECRET_KEYS:
            self.assertIn(k, s._data)


# ---------------------------------------------------- catálogo publicado
def sample_entries():
    return [
        Entry("a/Zelda", name="Zelda", platform="N64", description="d", cover_title="Zelda"),
        Entry("b/Hot", name="Hot", platform="Xbox", adult=True, notes="n", asset_hint="win"),
    ]


class Published(unittest.TestCase):
    def test_build_and_parse_roundtrip(self):
        doc = published.build(sample_entries(), {"a/zelda": "covers/a_zelda.png"})
        entries = published.parse(doc)
        by = {e.key: e for e in entries}
        self.assertEqual(set(by), {"a/zelda", "b/hot"})
        self.assertTrue(by["b/hot"].adult)
        self.assertEqual(by["b/hot"].asset_hint, "win")
        self.assertEqual(by["a/zelda"].cover, published.CATALOG_URL.rsplit("/", 1)[0] + "/covers/a_zelda.png")
        self.assertEqual(by["a/zelda"].source, "catalog")

    def test_build_does_not_leak_local_state(self):
        e = Entry("a/b", name="B", stars=99, pushed_at="x", created_at="y", cover="C:/Users/me/cover.png")
        g = published.build([e])["games"][0]
        for k in ("stars", "pushed_at", "created_at", "source"):
            self.assertNotIn(k, g)
        self.assertNotIn("cover", g)  # caminho local nunca é publicado

    def test_parse_rejects_bad_documents(self):
        for bad in (None, [], {"schema": 2, "games": []}, {"schema": 1, "games": "x"}):
            with self.assertRaises(published.CatalogError):
                published.parse(bad)

    def test_parse_skips_bad_entries_and_dedupes(self):
        doc = {"schema": 1, "games": [
            {"repo": "ok/one", "name": "One"}, {"repo": "OK/One"}, {"repo": "not a repo"},
            "string", {"repo": "x/y", "asset_hint": "(unclosed"},
            {"repo": "p/q", "cover": "http://insecure/x.png"},
            {"repo": "r/s", "cover": "file:///etc/passwd"},
            {"repo": "t/u", "cover": "../../evil.png"},
            {"repo": "v/w", "cover": "https://images.igdb.com/a.jpg", "homepage": "javascript:alert(1)"},
        ]}
        by = {e.key: e for e in published.parse(doc)}
        self.assertEqual(set(by), {"ok/one", "x/y", "p/q", "r/s", "t/u", "v/w"})
        self.assertEqual(by["x/y"].asset_hint, "")
        self.assertEqual(by["p/q"].cover, "")
        self.assertEqual(by["r/s"].cover, "")
        self.assertEqual(by["t/u"].cover, "")
        self.assertEqual(by["v/w"].cover, "https://images.igdb.com/a.jpg")
        self.assertEqual(by["v/w"].homepage, "")

    def test_write_folder_ignores_unsafe_cover_names(self):
        tmp = Path(tempfile.mkdtemp())
        doc = published.build(sample_entries())
        out = published.write_folder(tmp, doc, {"covers/ok.png": b"x", "../escape.png": b"y",
                                                "covers/../../e2.png": b"z"})
        self.assertTrue(out.is_file())
        self.assertTrue((tmp / "covers" / "ok.png").is_file())
        self.assertFalse((tmp.parent / "escape.png").exists())

    def test_remote_refresh_cache_and_304(self):
        tmp = Path(tempfile.mkdtemp())
        doc = published.build(sample_entries())
        s = Session(Resp(200, doc, {"ETag": 'W/"1"'}), Resp(304, None, content=b""))
        rc = published.RemoteCatalog(tmp / "c.json", session=s)
        entries, msg = rc.refresh()
        self.assertEqual(len(entries), 2)
        self.assertIn("2 jogos", msg)
        self.assertEqual(len(rc.cached()[0]), 2)
        none, msg2 = rc.refresh()
        self.assertIsNone(none)
        self.assertEqual(s.calls[1][2]["headers"]["If-None-Match"], 'W/"1"')

    def test_remote_errors_keep_cache(self):
        import requests
        tmp = Path(tempfile.mkdtemp())
        doc = published.build(sample_entries())
        rc = published.RemoteCatalog(tmp / "c.json", session=Session(Resp(200, doc)))
        rc.refresh()
        for resp in (requests.ConnectionError("x"), Resp(404, {}), Resp(500, {}),
                     Resp(200, {"schema": 9, "games": []}), Resp(200, None, content=b"not json"),
                     Resp(200, {}, content=b"x" * (published.MAX_BYTES + 1))):
            rc2 = published.RemoteCatalog(tmp / "c.json", session=Session(resp))
            got, msg = rc2.refresh()
            self.assertIsNone(got, msg)
            self.assertEqual(len(rc2.cached()[0]), 2)  # a cópia boa continua valendo

    def test_publish_creates_updates_and_skips(self):
        new_blob, same_blob = b"novo", b"igual"
        s = Session(
            Resp(404, {}),  # catalog.json não existe
            Resp(201, {}),
            Resp(200, {"sha": "abc", "content": base64.b64encode(same_blob).decode()}),  # igual: pula
            Resp(200, {"sha": "def", "content": base64.b64encode(b"antigo").decode()}),
            Resp(200, {}),
        )
        n = published.publish_to_github("tok", {"catalog/catalog.json": new_blob,
                                                "catalog/covers/a.png": same_blob,
                                                "catalog/covers/b.png": new_blob}, "msg", session=s)
        self.assertEqual(n, 2)
        puts = [c for c in s.calls if c[0] == "PUT"]
        self.assertNotIn("sha", puts[0][2]["json"])
        self.assertEqual(puts[1][2]["json"]["sha"], "def")
        self.assertEqual(s.headers["Authorization"], "Bearer tok")

    def test_publish_needs_token_and_reports_denied(self):
        with self.assertRaises(published.CatalogError):
            published.publish_to_github("", {"a": b"x"}, "m", session=Session())
        with self.assertRaises(published.CatalogError) as cm:
            published.publish_to_github("t", {"a": b"x"}, "m", session=Session(Resp(404, {}), Resp(403, {})))
        self.assertIn("permissão", str(cm.exception))


# --------------------------------------------------- tempo, status e notas
class FakeSettings:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v


class FakeProc:
    def __init__(self):
        import threading
        self.done = threading.Event()

    def wait(self):
        self.done.wait(5)


class Tracking(unittest.TestCase):
    def test_format(self):
        self.assertEqual(tracking.fmt_playtime(0), "0 min")
        self.assertEqual(tracking.fmt_playtime(30), "menos de 1 min")
        self.assertEqual(tracking.fmt_playtime(25 * 60), "25 min")
        self.assertEqual(tracking.fmt_playtime(3600), "1 h")
        self.assertEqual(tracking.fmt_playtime(3600 * 2 + 5 * 60), "2 h 05 min")

    def test_status_and_note(self):
        s = FakeSettings()
        tracking.set_meta(s, "A/B", status="playing", note="  gostei  ")
        self.assertEqual(tracking.status_of(s, "a/b"), "playing")
        self.assertEqual(tracking.note_of(s, "a/b"), "gostei")
        tracking.set_meta(s, "a/b", status="invalido")
        self.assertEqual(tracking.status_of(s, "a/b"), "")
        tracking.set_meta(s, "a/b", note="")
        self.assertEqual(s.get("game_meta"), {})  # sem nada, a entrada some

    def test_tracker_credits_when_game_closes(self):
        import time
        clock = [100.0]
        got = []
        t = tracking.PlayTracker(lambda r, secs: got.append((r, secs)), clock=lambda: clock[0])
        p = FakeProc()
        self.assertTrue(t.track("a/b", p))
        self.assertTrue(t.is_running("a/b"))
        clock[0] = 100.0 + 600
        p.done.set()
        for _ in range(100):
            if got:
                break
            time.sleep(0.02)
        self.assertEqual(got, [("a/b", 600.0)])
        self.assertFalse(t.is_running("a/b"))

    def test_short_sessions_and_elevated_launch_are_ignored(self):
        import time
        got = []
        clock = [0.0]
        t = tracking.PlayTracker(lambda r, s: got.append(s), clock=lambda: clock[0])
        p = FakeProc()
        t.track("a/b", p)
        clock[0] = 3
        p.done.set()
        time.sleep(0.2)
        self.assertEqual(got, [])
        self.assertFalse(t.track("x/y", None))

    def test_stop_all_credits_and_prevents_double_count(self):
        import time
        clock = [0.0]
        got = []
        t = tracking.PlayTracker(lambda r, s: got.append(s), clock=lambda: clock[0])
        p = FakeProc()
        t.track("a/b", p)
        clock[0] = 120
        self.assertEqual(t.stop_all(), {"a/b": 120})
        p.done.set()
        time.sleep(0.2)
        self.assertEqual(got, [])  # a thread não conta de novo

    def test_library_playtime_and_versions(self):
        lib = Library(Path(tempfile.mkdtemp()) / "l.json")
        lib.record_install("a/b", "B", "v1", "/g/v1", "/g/v1/b.exe", "/g")
        lib.record_install("a/b", "B", "v2", "/g/v2", "/g/v2/b.exe", "/g")
        lib.add_playtime("a/b", 90)
        lib.add_playtime("a/b", 30)
        self.assertEqual(lib.get("a/b")["playtime"], 120)
        self.assertTrue(lib.use_version("a/b", "v1"))
        rec = lib.get("a/b")
        self.assertEqual((rec["version"], rec["folder"], rec["exe"]), ("v1", "/g/v1", "/g/v1/b.exe"))
        self.assertFalse(lib.use_version("a/b", "v9"))
        self.assertFalse(lib.use_version("x/y", "v1"))
        again = Library(lib.path)
        self.assertEqual(again.get("a/b")["version"], "v1")

    def test_sort_by_playtime_and_status(self):
        class R:
            def __init__(self, name, rec=None, status=""):
                self.entry = Entry("a/" + name, name=name)
                self.rec, self.status, self.release = rec, status, None

        rows = [R("c"), R("a", {"playtime": 60}), R("b", {"playtime": 600})]
        out = sorting.sort_rows(rows, "playtime", lambda k: False)
        self.assertEqual([r.entry.name for r in out], ["b", "a", "c"])
        rows = [R("a", status="done"), R("b", status="playing"), R("c"), R("d", status="want")]
        out = sorting.sort_rows(rows, "status", lambda k: False)
        self.assertEqual([r.entry.name for r in out], ["b", "d", "a", "c"])


# ------------------------------------------------------------------ atalhos
class Steam(unittest.TestCase):
    def test_roundtrip_and_no_duplicate(self):
        data, added = shortcuts.add_to_vdf(None, "Zelda", "C:/g/z.exe", "C:/g", "-fullscreen")
        self.assertTrue(added)
        parsed = shortcuts.parse_vdf(data)
        item = parsed["shortcuts"]["0"]
        self.assertEqual(item["AppName"], "Zelda")
        self.assertEqual(item["Exe"], '"C:/g/z.exe"')
        self.assertEqual(item["StartDir"], '"C:/g"')
        self.assertEqual(item["LaunchOptions"], "-fullscreen")
        self.assertTrue(item["appid"] & 0x80000000)
        self.assertEqual(shortcuts.dump_vdf(parsed), data)
        again, added2 = shortcuts.add_to_vdf(data, "Zelda", "C:/g/z.exe", "C:/g")
        self.assertFalse(added2)
        self.assertEqual(again, data)
        more, added3 = shortcuts.add_to_vdf(data, "Mario", "C:/g/m.exe", "C:/g")
        self.assertTrue(added3)
        self.assertEqual(list(shortcuts.parse_vdf(more)["shortcuts"]), ["0", "1"])

    def test_keeps_other_shortcuts_untouched(self):
        existing = shortcuts.dump_vdf({"shortcuts": {"0": {"appid": 5, "AppName": "Outro", "Exe": '"x.exe"',
                                                           "tags": {"0": "fav"}}}})
        new, added = shortcuts.add_to_vdf(existing, "Zelda", "C:/g/z.exe", "C:/g")
        parsed = shortcuts.parse_vdf(new)
        self.assertTrue(added)
        self.assertEqual(parsed["shortcuts"]["0"]["tags"]["0"], "fav")
        self.assertEqual(parsed["shortcuts"]["0"]["AppName"], "Outro")

    def test_corrupt_file_is_refused(self):
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.add_to_vdf(b"\x05garbage", "A", "C:/a.exe", "C:/")
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.parse_vdf(b"\x00shortcuts")  # truncado

    def test_add_to_steam_backs_up_and_writes_each_account(self):
        base = Path(tempfile.mkdtemp())
        exe = base / "g" / "z.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"MZ")
        cfg = base / "userdata" / "1234" / "config"
        cfg.mkdir(parents=True)
        original = shortcuts.dump_vdf({"shortcuts": {}})
        (cfg / "shortcuts.vdf").write_bytes(original)
        (base / "userdata" / "5678").mkdir()  # conta sem arquivo ainda
        (base / "userdata" / "0").mkdir()  # pasta especial: ignorada
        changed = shortcuts.add_to_steam("Zelda", exe, base=base, running=False)
        self.assertEqual(len(changed), 2)
        baks = list(cfg.glob("shortcuts.vdf.usagi-*.bak"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_bytes(), original)
        self.assertIn("Zelda", shortcuts.parse_vdf((cfg / "shortcuts.vdf").read_bytes())["shortcuts"]["0"]["AppName"])
        self.assertFalse((base / "userdata" / "0" / "config").exists())
        self.assertEqual(shortcuts.add_to_steam("Zelda", exe, base=base, running=False), [])

    def test_refuses_while_steam_runs_or_missing(self):
        base = Path(tempfile.mkdtemp())
        exe = base / "z.exe"
        exe.write_bytes(b"MZ")
        (base / "userdata" / "1").mkdir(parents=True)
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.add_to_steam("Z", exe, base=base, running=True)
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.add_to_steam("Z", base / "nao.exe", base=base, running=False)
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.add_to_steam("Z", exe, base=Path(tempfile.mkdtemp()), running=False)

    def test_desktop_shortcut_off_windows_is_clear_error(self):
        if sys.platform != "win32":
            with self.assertRaises(shortcuts.ShortcutError):
                shortcuts.create_desktop_shortcut("Z", __file__)

    def test_file_name_is_cleaned(self):
        self.assertEqual(shortcuts._safe_file_name('Zelda: "MM" / 64?'), "Zelda MM 64")
        self.assertEqual(shortcuts._safe_file_name("..."), "Jogo")


# ------------------------------------------------------------------- reparo
class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def download(self, url, dest, progress=None, cancel=None, expected_size=0):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.payload)
        return Path(dest)


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    return buf.getvalue()


class Repair(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.folder = self.tmp / "game" / "v1"
        (self.folder / "saves").mkdir(parents=True)
        (self.folder / "game.exe").write_bytes(b"CORRUPTED")
        (self.folder / "saves" / "slot1.sav").write_bytes(b"MEU PROGRESSO")
        (self.folder / "config.ini").write_text("meu ajuste")
        self.downloads = self.tmp / "dl"

    def _run(self, payload, digest=""):
        asset = Asset("Game-Windows.zip", len(payload), "u", digest=digest)
        rel = Release("v1", "v1", "", False, "", "", [asset])
        return installer.repair(FakeClient(payload), rel, asset, self.folder, self.downloads)

    def test_restores_binaries_keeps_saves(self):
        payload = zip_bytes({"game.exe": b"GOOD", "saves/slot1.sav": b"VAZIO", "data/new.pak": b"P",
                             "config.ini": "padrao"})
        n = self._run(payload)
        self.assertEqual((self.folder / "game.exe").read_bytes(), b"GOOD")
        self.assertEqual((self.folder / "saves" / "slot1.sav").read_bytes(), b"MEU PROGRESSO")
        self.assertEqual((self.folder / "data" / "new.pak").read_bytes(), b"P")
        self.assertEqual((self.folder / "config.ini").read_text(), "padrao")  # configs voltam ao padrão
        self.assertEqual(n, 3)
        self.assertFalse(self.folder.with_name("v1.reparo").exists())
        self.assertEqual(list(self.downloads.glob("*")), [])

    def test_bad_checksum_changes_nothing(self):
        payload = zip_bytes({"game.exe": b"GOOD"})
        with self.assertRaises(InstallError):
            self._run(payload, digest="sha256:" + "0" * 64)
        self.assertEqual((self.folder / "game.exe").read_bytes(), b"CORRUPTED")

    def test_good_checksum_passes(self):
        payload = zip_bytes({"game.exe": b"GOOD"})
        self._run(payload, digest="sha256:" + hashlib.sha256(payload).hexdigest())
        self.assertEqual((self.folder / "game.exe").read_bytes(), b"GOOD")

    def test_unsafe_zip_changes_nothing(self):
        payload = zip_bytes({"../evil.exe": b"X"})
        with self.assertRaises(InstallError):
            self._run(payload)
        self.assertFalse((self.tmp / "game" / "evil.exe").exists())
        self.assertEqual((self.folder / "game.exe").read_bytes(), b"CORRUPTED")


if __name__ == "__main__":
    unittest.main()
