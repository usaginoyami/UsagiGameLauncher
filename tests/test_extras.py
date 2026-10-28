"""Segurança, filtro adulto, backup de saves e autoatualização."""
import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usagi_launcher import backup, catalog, safety, selfupdate, stars  # noqa: E402
from usagi_launcher.catalog import Entry  # noqa: E402
from usagi_launcher.errors import InstallError  # noqa: E402
from usagi_launcher.github_api import Asset, Release  # noqa: E402


def asset(name="Game-Windows-x64.zip", digest="sha256:" + "a" * 64):
    return Asset(name, 10, "https://x.invalid/a", digest=digest)


class Safety(unittest.TestCase):
    def test_curated_game_does_not_interrupt(self):
        e = Entry("Zelda64Recomp/Zelda64Recomp", source="catalog")
        self.assertFalse(safety.needs_confirmation(safety.assess(e, None, asset())))

    def test_small_catalog_project_is_only_a_note(self):
        e = Entry("a/b", source="catalog", notes="Projeto pequeno ou de conta pouco conhecida: confira")
        f = safety.assess(e, None, asset())
        self.assertEqual(safety.top_level(f), safety.LOW)
        self.assertFalse(safety.needs_confirmation(f))

    def test_discovered_low_stars_asks(self):
        e = Entry("someone/Cool-Recomp", source="discovered", stars=3)
        self.assertTrue(safety.needs_confirmation(safety.assess(e, None, asset())))

    def test_double_extension_and_crack_are_high(self):
        e = Entry("a/b", source="catalog")
        self.assertEqual(safety.top_level(safety.assess(e, None, asset("game.pdf.exe"))), safety.HIGH)
        self.assertEqual(safety.top_level(safety.assess(e, None, asset("Game-crack.zip"))), safety.HIGH)

    def test_bare_exe_package_asks(self):
        e = Entry("a/b", source="catalog")
        self.assertTrue(safety.needs_confirmation(safety.assess(e, None, asset("Setup.exe"))))

    def test_generated_owner_and_fake_port_name(self):
        e = Entry("cynthyfibrillose74/Zelda-TP-PC-Port", source="discovered", stars=500)
        texts = " ".join(f.text for f in safety.assess(e, None, asset()))
        self.assertIn("gerado", texts)
        self.assertIn("port nativo", texts)

    def test_new_repo_flagged(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        e = Entry("a/b", source="discovered", stars=900, created_at=now)
        self.assertIn("criado há", safety.summary(safety.assess(e, None, asset())))

    def test_missing_digest_is_low_only(self):
        e = Entry("a/b", source="catalog")
        f = safety.assess(e, None, asset(digest=""))
        self.assertEqual(safety.top_level(f), safety.LOW)


class Adult(unittest.TestCase):
    def test_detection(self):
        self.assertTrue(catalog.is_adult_repo("Peeping-Dorm-Manager", ""))
        self.assertTrue(catalog.is_adult_repo("x", "an NSFW recomp"))
        self.assertTrue(catalog.is_adult_repo("x", "", ["hentai"]))
        self.assertFalse(catalog.is_adult_repo("Sussex-Recomp", "Essex county game"))
        self.assertFalse(catalog.is_adult_repo("Zelda64Recomp", "Majora's Mask"))

    def test_merge_hides_but_never_user_or_installed(self):
        a = Entry("a/adult", source="catalog", adult=True)
        d = Entry("b/disc", source="discovered", adult=True)
        u = Entry("c/mine", source="user", adult=True)
        inst = Entry("e/installed", source="catalog", adult=True)
        ok = Entry("f/fine", source="catalog")
        shown = {e.key for e in catalog.merge([u], [a, inst, ok], [d], show_adult=False, keep={"e/installed"})}
        self.assertEqual(shown, {"c/mine", "e/installed", "f/fine"})
        every = {e.key for e in catalog.merge([u], [a, inst, ok], [d], show_adult=True)}
        self.assertEqual(len(every), 5)

    def test_bundled_catalog_flags(self):
        flagged = {e.repo for e in catalog.load_bundled() if e.adult}
        self.assertIn("NoRain211/doaxvb-re", flagged)
        self.assertNotIn("Zelda64Recomp/Zelda64Recomp", flagged)


class Backup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = self.tmp / "Game" / "v1"
        for rel, data in {"saves/slot1.sav": b"S1", "Game.exe": b"MZ", "config.toml": b"c=1",
                          "mods/m.cfg": b"m", "data/assets.bin": b"x", "a.srm": b"srm"}.items():
            p = self.old / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def test_classify(self):
        c = backup.classify
        self.assertEqual(c(Path("saves/x.dat")), "save")
        self.assertEqual(c(Path("deep/Save/x.dat")), "save")
        self.assertEqual(c(Path("a.srm")), "save")
        self.assertEqual(c(Path("config.toml")), "config")
        self.assertEqual(c(Path("mods/a.zip")), "config")
        self.assertIsNone(c(Path("Game.exe")))
        self.assertIsNone(c(Path("data/assets.bin")))

    def test_backup_zip_has_saves_and_config(self):
        z = backup.make_backup(self.old, self.tmp / "Game" / backup.BACKUP_DIR, "v1")
        self.assertTrue(z and z.is_file())
        names = set(zipfile.ZipFile(z).namelist())
        self.assertEqual(names, {"saves/slot1.sav", "config.toml", "mods/m.cfg", "a.srm"})

    def test_no_backup_when_nothing_to_save(self):
        empty = self.tmp / "E"
        (empty / "x").mkdir(parents=True)
        (empty / "x" / "Game.exe").write_bytes(b"MZ")
        self.assertIsNone(backup.make_backup(empty, self.tmp / "b", "v"))

    def test_carry_forward_only_saves_without_overwriting(self):
        new = self.tmp / "Game" / "v2"
        (new / "saves").mkdir(parents=True)
        (new / "saves" / "slot1.sav").write_bytes(b"NEW")
        (new / "config.toml").write_bytes(b"c=2")
        n = backup.carry_forward(self.old, new)
        self.assertEqual(n, 1)  # só a.srm: slot1.sav já existia na pasta nova
        self.assertEqual((new / "saves" / "slot1.sav").read_bytes(), b"NEW")
        self.assertEqual((new / "a.srm").read_bytes(), b"srm")
        self.assertEqual((new / "config.toml").read_bytes(), b"c=2")  # configuração não migra
        self.assertFalse((new / "mods").exists())

    def test_carry_forward_same_folder_is_noop(self):
        self.assertEqual(backup.carry_forward(self.old, self.old), 0)

    def test_restore_and_zip_slip(self):
        z = backup.make_backup(self.old, self.tmp / "b", "v1")
        target = self.tmp / "restored"
        self.assertEqual(backup.restore(z, target), 4)
        self.assertEqual((target / "saves" / "slot1.sav").read_bytes(), b"S1")
        evil = self.tmp / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zz:
            zz.writestr("../../escape.txt", "x")
            zz.writestr("ok.txt", "y")
        self.assertEqual(backup.restore(evil, target), 1)
        self.assertFalse((self.tmp / "escape.txt").exists())

    def test_prune_keeps_five(self):
        d = self.tmp / "p"
        d.mkdir()
        for i in range(8):
            (d / f"2026010{i}-000000_v.zip").write_bytes(b"x")
        backup.prune(d)
        self.assertEqual(len(list(d.glob("*.zip"))), backup.KEEP_BACKUPS)
        self.assertTrue((d / "20260107-000000_v.zip").exists())  # os mais novos ficam

    def test_symlinks_are_not_followed(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.sav").write_bytes(b"secret")
        try:
            (self.old / "saves" / "link").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("sem suporte a symlink")
        rels = [str(p) for p in backup.collect(self.old)["save"]]
        self.assertFalse(any("secret" in r for r in rels))


class FakeClient:
    def __init__(self, rel, payload=b""):
        self.rel, self.payload = rel, payload

    def latest_release(self, repo, allow_prerelease=True):
        return self.rel

    def download(self, url, dest, progress=None, cancel=None, expected_size=0):
        Path(dest).write_bytes(self.payload)
        return Path(dest)


def make_release(tag, payload=b"", digest=""):
    a = Asset("UsagiGameLauncher-Windows-x64.zip", len(payload), "https://x.invalid/u.zip", digest=digest)
    return Release(tag, tag, "2026-10-01T00:00:00Z", False, "https://github.com/x/y/releases/tag/" + tag, "", [a])


def zip_with(*names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, b"MZ")
    return buf.getvalue()


class SelfUpdate(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(selfupdate.parse_version("v1.2.3"), (1, 2, 3))
        self.assertTrue(selfupdate.is_newer("v1.0.1", "1.0.0"))
        self.assertTrue(selfupdate.is_newer("v1.10.0", "1.9.0"))
        self.assertFalse(selfupdate.is_newer("v1.0.0", "1.0.0"))
        self.assertFalse(selfupdate.is_newer("v1.0", "1.0.0"))
        self.assertFalse(selfupdate.is_newer("v0.9.9", "1.0.0"))
        self.assertFalse(selfupdate.is_newer("latest", "1.0.0"))

    def test_find_update(self):
        self.assertIsNone(selfupdate.find_update(FakeClient(make_release("v1.0.0"))))
        self.assertIsNone(selfupdate.find_update(FakeClient(None)))
        rel, asset_ = selfupdate.find_update(FakeClient(make_release("v2.0.0")))
        self.assertEqual(rel.tag, "v2.0.0")
        self.assertEqual(asset_.name, "UsagiGameLauncher-Windows-x64.zip")

    def test_download_extracts_and_finds_exe(self):
        payload = zip_with("UsagiGameLauncher/UsagiGameLauncher.exe", "UsagiGameLauncher/data/catalog.json")
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        rel = make_release("v2.0.0", payload, digest)
        out = selfupdate.download_update(FakeClient(rel, payload), rel.assets[0], Path(tempfile.mkdtemp()) / "u")
        self.assertTrue((out / "UsagiGameLauncher.exe").is_file())
        self.assertTrue((out / "data" / "catalog.json").is_file())

    def test_bad_digest_changes_nothing(self):
        payload = zip_with("UsagiGameLauncher.exe")
        rel = make_release("v2.0.0", payload, "sha256:" + "0" * 64)
        with self.assertRaises(InstallError):
            selfupdate.download_update(FakeClient(rel, payload), rel.assets[0], Path(tempfile.mkdtemp()) / "u")

    def test_zip_without_exe_rejected(self):
        payload = zip_with("readme.txt")
        rel = make_release("v2.0.0", payload)
        with self.assertRaises(InstallError):
            selfupdate.download_update(FakeClient(rel, payload), rel.assets[0], Path(tempfile.mkdtemp()) / "u")

    def test_updater_script(self):
        bat = selfupdate.build_updater_script(Path("C:/tmp/novo"), Path("C:/Apps/Usagi"), 4242)
        self.assertIn('tasklist /FI "PID eq 4242"', bat)
        self.assertIn("xcopy", bat)
        self.assertIn("UsagiGameLauncher.exe", bat)
        self.assertIn("\r\n", bat)
        self.assertNotIn("del /s", bat.lower())  # nunca apaga a pasta do programa


class Stars(unittest.TestCase):
    def test_format(self):
        self.assertEqual(stars.format_stars(0), "0")
        self.assertEqual(stars.format_stars(56), "56")
        self.assertEqual(stars.format_stars(999), "999")
        self.assertEqual(stars.format_stars(1000), "1 mil")
        self.assertEqual(stars.format_stars(1234), "1,2 mil")
        self.assertEqual(stars.format_stars(15600), "15,6 mil")
        self.assertEqual(stars.format_stars(1_500_000), "1,5 mi")

    def test_cache_roundtrip_and_stale(self):
        path = Path(tempfile.mkdtemp()) / "stars.json"
        c = stars.StarCache(path)
        self.assertIsNone(c.get("A/B"))
        self.assertTrue(c.is_stale("A/B"))
        c.set("A/B", 42)
        c.save()
        c2 = stars.StarCache(path)
        self.assertEqual(c2.get("a/b"), 42)
        self.assertFalse(c2.is_stale("a/b"))
        c2._d["a/b"]["ts"] = 0
        self.assertTrue(c2.is_stale("a/b"))

    def test_corrupt_file_ignored(self):
        path = Path(tempfile.mkdtemp()) / "stars.json"
        path.write_text("{ nao e json")
        self.assertIsNone(stars.StarCache(path).get("a/b"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
