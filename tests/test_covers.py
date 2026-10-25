import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usagi_launcher import covers  # noqa: E402
from usagi_launcher.catalog import Entry  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class Resp:
    def __init__(self, status=200, data=None, body=b"", headers=None):
        self.status_code, self.ok = status, 200 <= status < 300
        self._data, self.content, self.headers = data, body, headers or {}
        self.text = body.decode("utf-8", "ignore")

    def json(self):
        return self._data

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]

    def close(self):
        pass


class Net:
    """Sessão falsa: a primeira rota cujo trecho aparece na URL responde."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        self.calls.append((url, dict(headers or {})))
        for part, resp in self.routes:
            if part in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return Resp(404)


def _post(self, url, params=None, headers=None, timeout=None, data=None):
    self.calls.append((url, dict(headers or {}), params, data))
    for part, resp in self.routes:
        if part in url:
            return resp
    return Resp(404)


Net.post = _post


def wiki(pages):
    return Resp(data={"query": {"pages": pages}})


class Helpers(unittest.TestCase):
    def test_sniff(self):
        self.assertEqual(covers.sniff(PNG), "png")
        self.assertEqual(covers.sniff(b"\xff\xd8\xff\xe0abc"), "jpg")
        self.assertEqual(covers.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "webp")
        self.assertIsNone(covers.sniff(b"<html>nope</html>"))

    def test_clean_title(self):
        self.assertEqual(covers.clean_title("Zelda 64: Recompiled"), "Zelda 64")
        self.assertEqual(covers.clean_title("Chameleon Twist: Recompiled (JP)"), "Chameleon Twist")
        self.assertEqual(covers.clean_title("CoolGame Recomp"), "CoolGame")
        self.assertEqual(covers.clean_title("Recomp"), "Recomp")

    def test_similarity(self):
        self.assertEqual(covers.similarity("Banjo-Kazooie", "Banjo-Kazooie"), 1.0)
        self.assertGreaterEqual(covers.similarity("Zelda: Majora's Mask",
                                                  "The Legend of Zelda: Majora's Mask"), 0.6)
        self.assertLess(covers.similarity("Quest 64", "Quest for Glory"), 0.6)

    def test_readme_image(self):
        md = ("# Jogo\n[![build](https://img.shields.io/x.svg)](u)\n"
              "![logo](./docs/cover.png)\n![outra](https://x/y.jpg)")
        self.assertEqual(covers.readme_image_url(md, "a/b"),
                         "https://raw.githubusercontent.com/a/b/HEAD/docs/cover.png")
        html = '<p><img width="300" src="https://github.com/user-attachments/assets/abc"></p>'
        self.assertEqual(covers.readme_image_url(html, "a/b"),
                         "https://github.com/user-attachments/assets/abc")
        self.assertEqual(covers.readme_image_url("![x](.github/img/banner.png)", "a/b"),
                         "https://raw.githubusercontent.com/a/b/HEAD/.github/img/banner.png")
        self.assertIsNone(covers.readme_image_url("só texto ![b](https://shields.io/b.svg)", "a/b"))


class Finder(unittest.TestCase):
    def test_wikipedia_skips_unrelated_result(self):
        net = Net([("wikipedia.org/w/api.php", wiki({
            "1": {"title": "Outro Jogo Qualquer", "index": 1, "thumbnail": {"source": "https://img/errada.png"}},
            "2": {"title": "Banjo-Kazooie", "index": 2, "thumbnail": {"source": "https://upload.wikimedia.org/b.png"}},
        })), ("upload.wikimedia.org/b.png", Resp(body=PNG))])
        data, err = covers.CoverFinder(net).find(Entry("a/b", cover_title="Banjo-Kazooie"))
        self.assertEqual((data, err), (PNG, False))
        self.assertFalse(any("errada" in u for u, _ in net.calls))

    def test_falls_back_to_readme(self):
        net = Net([("wikipedia.org", wiki({})),
                   ("raw.githubusercontent.com/a/b/HEAD/README.md", Resp(body=b"![c](art/capa.png)")),
                   ("HEAD/art/capa.png", Resp(body=PNG))])
        data, err = covers.CoverFinder(net).find(Entry("a/b", name="Obscuro"))
        self.assertEqual((data, err), (PNG, False))

    def test_steamgriddb_first_and_key_not_leaked(self):
        net = Net([
            ("search/autocomplete", Resp(data={"data": [{"id": 5, "name": "Banjo-Kazooie"}]})),
            ("grids/game/5", Resp(data={"data": [{"url": "https://cdn.sgdb/g.png"}]})),
            ("cdn.sgdb/g.png", Resp(body=PNG)),
        ])
        finder = covers.CoverFinder(net, sgdb_key="SEGREDO")
        data, _ = finder.find(Entry("a/b", cover_title="Banjo-Kazooie"))
        self.assertEqual(data, PNG)
        for url, headers in net.calls:
            if "steamgriddb.com" in url:
                self.assertEqual(headers.get("Authorization"), "Bearer SEGREDO")
            else:
                self.assertNotIn("Authorization", headers)
                self.assertNotIn("SEGREDO", url)
        self.assertFalse(any("wikipedia" in u for u, _ in net.calls))  # parou na primeira fonte

    def test_igdb_cover_and_secret_stays_in_twitch(self):
        net = Net([
            ("id.twitch.tv", Resp(data={"access_token": "TOK", "expires_in": 5000000})),
            ("api.igdb.com", Resp(data=[{"name": "Other", "cover": {"image_id": "zz"}},
                                         {"name": "Gran Turismo 4", "cover": {"image_id": "co1abc"}}])),
            ("t_cover_big/co1abc.jpg", Resp(body=PNG)),
        ])
        finder = covers.CoverFinder(net, igdb_id="CID", igdb_secret="SEGREDO")
        data, err = finder.find(Entry("a/b", cover_title="Gran Turismo 4"))
        self.assertEqual((data, err), (PNG, False))
        for call in net.calls:
            url, headers = call[0], call[1]
            blob = url + str(headers) + str(call[2:] if len(call) > 2 else "")
            if "id.twitch.tv" not in url:
                self.assertNotIn("SEGREDO", blob)
        self.assertFalse(any("wikipedia" in c[0] for c in net.calls))
        finder.find(Entry("a/b", cover_title="Gran Turismo 4"))  # reutiliza o token
        self.assertEqual(sum("id.twitch.tv" in c[0] for c in net.calls), 1)

    def test_igdb_needs_both_keys_and_falls_through(self):
        net = Net([("wikipedia.org", wiki({"1": {"title": "Quest 64", "index": 1,
                                                   "thumbnail": {"source": "https://x/p.png"}}})),
                   ("x/p.png", Resp(body=PNG))])
        finder = covers.CoverFinder(net, igdb_id="CID")
        self.assertIsNone(finder.igdb)
        self.assertEqual(finder.find(Entry("a/b", cover_title="Quest 64"))[0], PNG)

    def test_igdb_no_match_falls_to_wikipedia(self):
        net = Net([("id.twitch.tv", Resp(data={"access_token": "T", "expires_in": 9999})),
                   ("api.igdb.com", Resp(data=[{"name": "Totally Different", "cover": {"image_id": "q"}}])),
                   ("wikipedia.org", wiki({"1": {"title": "Quest 64", "index": 1,
                                                   "thumbnail": {"source": "https://x/p.png"}}})),
                   ("x/p.png", Resp(body=PNG))])
        finder = covers.CoverFinder(net, igdb_id="C", igdb_secret="S")
        self.assertEqual(finder.find(Entry("a/b", cover_title="Quest 64"))[0], PNG)

    def test_html_is_not_an_image(self):
        net = Net([("wikipedia.org", wiki({"1": {"title": "Quest 64", "index": 1,
                                                  "thumbnail": {"source": "https://x/p.png"}}})),
                   ("x/p.png", Resp(body=b"<html>erro</html>"))])
        self.assertEqual(covers.CoverFinder(net).find(Entry("a/b", cover_title="Quest 64")), (None, False))

    def test_huge_image_rejected(self):
        net = Net([("x/big.png", Resp(body=PNG, headers={"Content-Length": str(covers.MAX_BYTES + 1)}))])
        self.assertIsNone(covers.download_image(net, "https://x/big.png"))

    def test_network_error_flagged(self):
        net = Net([("", requests.ConnectionError("sem rede"))])
        self.assertEqual(covers.CoverFinder(net).find(Entry("a/b", name="Jogo")), (None, True))

    def test_disabled_does_nothing(self):
        net = Net([])
        self.assertEqual(covers.CoverFinder(net, enabled=False).find(Entry("a/b")), (None, True))
        self.assertEqual(net.calls, [])

    def test_custom_cover_wins(self):
        net = Net([("meu.png", Resp(body=PNG))])
        data, _ = covers.CoverFinder(net).find(Entry("a/b", cover="https://x/meu.png"))
        self.assertEqual(data, PNG)
        self.assertEqual(len(net.calls), 1)


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def store(self, routes):
        return covers.CoverStore(self.tmp / "c", covers.CoverFinder(Net(routes)))

    def test_fetch_caches_on_disk(self):
        s = self.store([("wikipedia.org", wiki({"1": {"title": "Quest 64", "index": 1,
                                                       "thumbnail": {"source": "https://x/p.png"}}})),
                        ("x/p.png", Resp(body=PNG))])
        e = Entry("Rainchus/Quest64-Recomp", cover_title="Quest 64")
        self.assertTrue(s.needs_fetch(e.key))
        path = s.fetch(e)
        self.assertEqual(path.read_bytes(), PNG)
        self.assertEqual(s.cached(e.key), path)
        self.assertFalse(s.needs_fetch(e.key))

    def test_no_result_is_remembered_for_a_week(self):
        s = self.store([])
        e = Entry("a/b", name="Nada")
        self.assertIsNone(s.fetch(e))
        self.assertFalse(s.needs_fetch(e.key))
        old = time.time() - covers.RETRY_AFTER - 10
        os.utime(s._none(e.key), (old, old))
        self.assertTrue(s.needs_fetch(e.key))

    def test_offline_is_not_remembered(self):
        s = self.store([("", requests.ConnectionError("x"))])
        e = Entry("a/b", name="Nada")
        self.assertIsNone(s.fetch(e))
        self.assertTrue(s.needs_fetch(e.key))

    def test_set_custom_and_clear(self):
        s = self.store([])
        img = self.tmp / "m.png"
        img.write_bytes(PNG)
        s.set_custom("a/b", img)
        self.assertEqual(s.cached("a/b").read_bytes(), PNG)
        bad = self.tmp / "t.txt"
        bad.write_text("não sou imagem")
        with self.assertRaises(ValueError):
            s.set_custom("a/b", bad)
        s.clear("a/b")
        self.assertIsNone(s.cached("a/b"))

    def test_clear_failures(self):
        s = self.store([])
        s.fetch(Entry("a/b", name="Nada"))
        s.clear_failures()
        self.assertTrue(s.needs_fetch("a/b"))

    def test_key_is_filename_safe(self):
        s = self.store([])
        s.set_custom("../../etc/x", self._png())
        self.assertTrue(all(p.parent == s.folder for p in s.folder.iterdir()))

    def _png(self):
        p = self.tmp / "k.png"
        p.write_bytes(PNG)
        return p


if __name__ == "__main__":
    unittest.main(verbosity=2)
