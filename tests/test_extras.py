"""Segurança, filtro adulto, backup de saves e autoatualização."""
import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usagi_launcher import backup, catalog, safety, scanner, selfupdate, sorting, stars  # noqa: E402
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

    def test_hidden_removes_catalog_but_not_user_entries(self):
        c = Entry("a/cat", source="catalog")
        d = Entry("b/disc", source="discovered")
        u = Entry("c/mine", source="user")
        shown = {e.key for e in catalog.merge([u], [c], [d], hidden={"a/cat", "b/disc", "c/mine"})}
        self.assertEqual(shown, {"c/mine"})

    def test_target_defaults_and_bundled_targets(self):
        self.assertEqual(Entry("a/b").target, "")
        self.assertIn("Windows", catalog.TARGETS)
        t = {e.repo: e.target for e in catalog.load_bundled() if e.target}
        self.assertEqual(t.get("antoxa2584x/burnout3-sw"), "Switch")
        self.assertEqual(t.get("Simeony2007/VibeRecomp-for-GoW-CoO"), "Portátil ARM64")

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


class ManualBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = self.tmp / "Game" / "v1"
        for rel, data in {"saves/s1.sav": b"S", "Game.exe": b"MZ", "data/a.pak": b"P" * 1000,
                          "config.toml": b"c", "_backups/old.zip": b"zzz"}.items():
            p = self.game / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def test_full_backup_has_everything_but_backups_dir(self):
        dest, n = backup.make_manual_backup(self.game, self.tmp / "out" / "full.zip", full=True)
        names = set(zipfile.ZipFile(dest).namelist())
        self.assertEqual(names, {"saves/s1.sav", "Game.exe", "data/a.pak", "config.toml"})
        self.assertEqual(n, 4)
        self.assertFalse(list((self.tmp / "out").glob("*.part")))

    def test_saves_only_backup(self):
        dest, n = backup.make_manual_backup(self.game, self.tmp / "s.zip", full=False)
        self.assertEqual(set(zipfile.ZipFile(dest).namelist()), {"saves/s1.sav", "config.toml"})

    def test_roundtrip_restore(self):
        dest, _ = backup.make_manual_backup(self.game, self.tmp / "f.zip", full=True)
        target = self.tmp / "novo"
        self.assertEqual(backup.restore(dest, target), 4)
        self.assertEqual((target / "data" / "a.pak").read_bytes(), b"P" * 1000)

    def test_refuses_destination_inside_the_game_folder(self):
        with self.assertRaises(backup.BackupError):
            backup.make_manual_backup(self.game, self.game / "saves" / "b.zip", full=True)

    def test_refuses_non_zip_name_and_empty_saves(self):
        with self.assertRaises(backup.BackupError):
            backup.make_manual_backup(self.game, self.tmp / "b.txt", full=True)
        empty = self.tmp / "E"
        (empty / "x").mkdir(parents=True)
        (empty / "x" / "Game.exe").write_bytes(b"MZ")
        with self.assertRaises(backup.BackupError):
            backup.make_manual_backup(empty, self.tmp / "e.zip", full=False)
        self.assertFalse((self.tmp / "e.zip").exists())

    def test_cancel_leaves_no_partial_file(self):
        import threading
        ev = threading.Event()
        ev.set()
        with self.assertRaises(backup.BackupError):
            backup.make_manual_backup(self.game, self.tmp / "c.zip", full=True, cancel=ev)
        self.assertEqual(list(self.tmp.glob("c.zip*")), [])

    def test_not_enough_space(self):
        real = backup.shutil.disk_usage
        backup.shutil.disk_usage = lambda p: type("U", (), {"free": 10})()
        try:
            with self.assertRaises(backup.BackupError) as cm:
                backup.make_manual_backup(self.game, self.tmp / "d.zip", full=True)
            self.assertIn("espaço", str(cm.exception))
        finally:
            backup.shutil.disk_usage = real
        self.assertEqual(list(self.tmp.glob("d.zip*")), [])

    def test_progress_reaches_total(self):
        seen = []
        backup.make_manual_backup(self.game, self.tmp / "p.zip", full=True, progress=lambda ph, d, t: seen.append((d, t)))
        self.assertEqual(seen[-1][0], seen[-1][1])
        self.assertGreater(seen[-1][1], 0)

    def test_folder_size_skips_backups(self):
        self.assertEqual(backup.folder_size(self.game), 1 + 2 + 1000 + 1)


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


class R:
    """Linha mínima com a mesma forma que a janela usa."""
    def __init__(self, name, stars=0, platform="N64", published="", played="", installed="",
                 update=False, source="catalog", key=None):
        self.entry = Entry(key or name.lower(), name=name, platform=platform, source=source, stars=stars)
        self.release = Release("v1", "v1", published, False, "", "", []) if published else None
        self.rec = {"last_played": played, "installed_at": installed} if (played or installed) else None
        if self.rec is not None:
            self.rec = {k: v for k, v in self.rec.items() if v}
        self.has_update = update

    @property
    def installed(self):
        return self.rec is not None


def names(rows, mode, favs=()):
    return [r.entry.name for r in sorting.sort_rows(rows, mode, lambda k: k in favs)]


class Sorting(unittest.TestCase):
    def setUp(self):
        self.rows = [
            R("beta", stars=50, platform="PS2", published="2026-03-01T00:00:00Z", played="2026-10-02"),
            R("Alpha", stars=900, platform="N64", published="2026-09-01T00:00:00Z", installed="2026-05-01"),
            R("gamma", stars=0, platform="Xbox"),
            R("Delta", stars=12, platform="N64", published="2025-01-01T00:00:00Z", played="2026-10-20",
              installed="2026-08-01", update=True),
        ]

    def test_name(self):
        self.assertEqual(names(self.rows, "name_az"), ["Alpha", "beta", "Delta", "gamma"])
        self.assertEqual(names(self.rows, "name_za"), ["gamma", "Delta", "beta", "Alpha"])

    def test_stars_unknown_last_in_both_directions(self):
        self.assertEqual(names(self.rows, "stars_desc"), ["Alpha", "beta", "Delta", "gamma"])
        self.assertEqual(names(self.rows, "stars_asc"), ["Delta", "beta", "Alpha", "gamma"])

    def test_platform_follows_catalog_order(self):
        self.assertEqual(names(self.rows, "platform"), ["Alpha", "Delta", "gamma", "beta"])  # N64, Xbox, PS2

    def test_release_and_played_and_installed(self):
        self.assertEqual(names(self.rows, "release_new"), ["Alpha", "beta", "Delta", "gamma"])
        self.assertEqual(names(self.rows, "played"), ["Delta", "beta", "Alpha", "gamma"])
        self.assertEqual(names(self.rows, "installed_new"), ["Delta", "Alpha", "beta", "gamma"])

    def test_default_puts_updates_installed_favorites_first(self):
        rows = self.rows + [R("Zed", key="zed")]
        self.assertEqual(names(rows, "default", favs={"zed"})[:4], ["Delta", "Alpha", "beta", "Zed"])

    def test_unknown_mode_falls_back_to_default(self):
        self.assertEqual(names(self.rows, "nao-existe"), names(self.rows, "default"))

    def test_all_modes_listed_once(self):
        self.assertEqual(len(sorting.MODE_KEYS), len(set(sorting.MODE_KEYS)))
        for m in sorting.MODE_KEYS:
            self.assertEqual(len(sorting.sort_rows(self.rows, m, lambda k: False)), 4)


def mkgame(base, rel, exe="Game.exe"):
    d = Path(base) / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / exe).write_bytes(b"MZ" + b"0" * 64)
    return d


class Scanner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.entries = [Entry("Zelda64Recomp/Zelda64Recomp", name="Zelda 64: Recompiled"),
                        Entry("BanjoRecomp/BanjoRecomp", name="Banjo: Recompiled"),
                        Entry("a/GT4", name="Gran Turismo 4", cover_title="Gran Turismo 4")]

    def test_describe(self):
        self.assertEqual(scanner.describe(Path("/g/Zelda/v1.2.0"))[1:], ("v1.2.0", "Zelda"))
        self.assertEqual(scanner.describe(Path("/g/Zelda64Recompiled"))[1:], ("manual", "Zelda64Recompiled"))
        self.assertEqual(scanner.describe(Path("/g/Game/1.0"))[1], "1.0")

    def test_name_score(self):
        self.assertEqual(scanner.name_score("Zelda64Recompiled", "Zelda64Recomp"), 1.0)
        self.assertEqual(scanner.name_score("Banjo-Recomp", "BanjoRecomp"), 1.0)
        self.assertEqual(scanner.name_score("Space", "PC"), 0.0)  # ruído só conta como palavra inteira
        self.assertEqual(scanner.name_score("Capcom", "Cap"), 0.0)

    def test_manual_folder_matches_by_repo_name(self):
        mkgame(self.tmp, "Zelda64Recompiled", "Zelda64Recompiled.exe")
        found = scanner.scan([self.tmp], self.entries)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].match, "zelda64recomp/zelda64recomp")
        self.assertEqual(found[0].version, "manual")
        self.assertEqual(found[0].exe.name, "Zelda64Recompiled.exe")

    def test_versioned_layout_keeps_newest(self):
        mkgame(self.tmp, "Banjo/v1.0.0")
        mkgame(self.tmp, "Banjo/v1.10.0")
        mkgame(self.tmp, "Banjo/v1.2.0")
        found = scanner.scan([self.tmp], self.entries)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].version, "v1.10.0")
        self.assertEqual(found[0].root.name, "Banjo")
        self.assertEqual(found[0].match, "banjorecomp/banjorecomp")

    def test_ambiguous_match_is_left_for_the_user(self):
        entries = self.entries + [Entry("fork/GT4-fork", name="Gran Turismo 4", cover_title="Gran Turismo 4")]
        mkgame(self.tmp, "Gran Turismo 4")
        f = scanner.scan([self.tmp], entries)[0]
        self.assertIsNone(f.match)
        self.assertEqual(len(f.alternatives), 2)

    def test_unknown_folder_has_no_match_and_installers_are_ignored(self):
        mkgame(self.tmp, "Outro Jogo Qualquer")
        mkgame(self.tmp, "SoInstalador", "setup.exe")
        found = scanner.scan([self.tmp], self.entries)
        self.assertEqual([f.label for f in found], ["Outro Jogo Qualquer"])
        self.assertIsNone(found[0].match)

    def test_known_folders_and_depth_limit(self):
        g = mkgame(self.tmp, "Zelda64Recompiled")
        self.assertEqual(scanner.scan([self.tmp], self.entries, {str(g)}), [])
        mkgame(self.tmp, "a/b/c/d/Deep")
        self.assertEqual([f.label for f in scanner.scan([self.tmp], self.entries, {str(g)})], [])

    def test_does_not_follow_symlinks(self):
        outside = Path(tempfile.mkdtemp())
        mkgame(outside, "Escondido")
        try:
            (self.tmp / "link").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("sem symlink")
        self.assertEqual(scanner.scan([self.tmp], self.entries), [])

    def test_uses_original_catalog_name_not_the_renamed_one(self):
        e = Entry("x/y", name="Meu apelido")
        mkgame(self.tmp, "Nome Original")
        f = scanner.scan([self.tmp], [e], orig_names={"x/y": "Nome Original"})[0]
        self.assertEqual(f.match, "x/y")


if __name__ == "__main__":
    unittest.main(verbosity=1)
