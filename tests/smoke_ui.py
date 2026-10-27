"""Teste de interface sem tela (offscreen): lista, releases, instalação e capturas."""
import io
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from usagi_launcher import catalog, paths  # noqa: E402
from usagi_launcher.errors import GitHubError  # noqa: E402
from usagi_launcher.github_api import Asset, GitHubClient, Release  # noqa: E402
from usagi_launcher.library import Library  # noqa: E402
from usagi_launcher.settings import Settings  # noqa: E402
from usagi_launcher.ui import theme  # noqa: E402
from usagi_launcher.ui.main_window import MainWindow  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("Game/Game.exe", b"MZ" + b"0" * 2048)
PAYLOAD = buf.getvalue()


class FakeClient(GitHubClient):
    def __init__(self):
        super().__init__("tok")
        self.searches = 0

    def latest_release(self, full_name, allow_prerelease=True):
        time.sleep(0.05)
        low = full_name.lower()
        if low == "klorfmorf/goemon64recomp":
            return None  # sem releases
        if low == "sonicdcer/dnzhrecomp":
            raise GitHubError("GitHub respondeu 500")
        tag = "v2.0.0" if low == "banjorecomp/banjorecomp" else "v1.0.0"
        a = Asset("Game-Windows-x64.zip", len(PAYLOAD), "https://x.invalid/g.zip")
        l = Asset("Game-Linux-x64.zip", 123, "https://x.invalid/l.zip")
        return Release(tag, tag, "2026-09-30T12:00:00Z", low.startswith("rainchus"),
                       f"https://github.com/{full_name}/releases/tag/{tag}",
                       "## Novidades\n\n- Suporte a 120 FPS\n- Correções de áudio\n", [l, a])

    def search_repos(self, topic, min_stars=10, per_page=50):
        self.searches += 1
        return [{"full_name": "someone/CoolGame-Recomp", "name": "CoolGame-Recomp",
                 "description": "Recompilation of a PS3 classic", "stargazers_count": 321,
                 "topics": ["recomp", "ps3"]},
                {"full_name": "someone/N64Recomp", "name": "N64Recomp", "description": "tool",
                 "stargazers_count": 999}]

    def download(self, url, dest, progress=None, cancel=None, expected_size=0):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PAYLOAD)
        if progress:
            progress(len(PAYLOAD), len(PAYLOAD))
        return dest


def pump(app, seconds=1.0, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        QCoreApplication.sendPostedEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return until() if until else True


tmp = Path(tempfile.mkdtemp())
paths.set_data_dir(tmp / "data")
app = QApplication([])
app.setStyleSheet(theme.QSS)

settings = Settings()
settings.set("install_dir", str(tmp / "games"))
lib = Library()
lib.record_install("BanjoRecomp/BanjoRecomp", "Banjo: Recompiled", "v1.0.0",
                   str(tmp / "games/Banjo/v1.0.0"), str(tmp / "games/Banjo/v1.0.0/b.exe"), str(tmp / "games/Banjo"))
settings.toggle("favorites", "zelda64recomp/zelda64recomp")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402


def make_png(w, h, color, text):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    p = QPainter(img)
    p.setPen(QColor("white"))
    f = QFont()
    f.setPixelSize(max(h // 6, 10))
    f.setBold(True)
    p.setFont(f)
    p.drawText(img.rect(), 0x84, text)  # centralizado
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


class FakeFinder:
    """Banjo e Zelda: caixa vertical; Mario Kart e Goemon: banner; Bomberman: sem capa."""
    enabled = True

    def __init__(self):
        self.asked = []

    def configure(self, key, enabled, *extra):
        pass

    def find(self, entry):
        self.asked.append(entry.key)
        n = entry.name.lower()
        if "bomberman" in n:
            return None, False
        if "banjo" in n or "zelda" in n:
            return make_png(300, 420, "#2e5eaa" if "banjo" in n else "#2a7a4a", entry.cover_title[:14]), False
        return make_png(920, 430, "#a23b2a", entry.cover_title[:18]), False


client = FakeClient()
finder = FakeFinder()
win = MainWindow(client, settings, lib, cover_finder=finder, auto_refresh=True)
win.show()

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


pump(app, 4, until=lambda: win.pending == 0 and not win.discovering and client.searches > 0)
pump(app, 0.3)
check(win.pending == 0, "todas as consultas de release terminaram")
check(win.rows["banjorecomp/banjorecomp"].has_update, "Banjo instalado v1.0.0 -> atualização v2.0.0 detectada")
check(win.rows["klorfmorf/goemon64recomp"].loaded and win.rows["klorfmorf/goemon64recomp"].release is None,
      "repositório sem release tratado")
check("500" in win.rows["sonicdcer/dnzhrecomp"].error, "erro de API guardado na linha")
check("someone/coolgame-recomp" in win.rows, "projeto descoberto apareceu")
check("someone/n64recomp" not in win.rows, "ferramenta N64Recomp filtrada da descoberta")
check(win.rows["someone/coolgame-recomp"].entry.platform == "PS3", "plataforma PS3 deduzida")
check(win.list.count() == len(win.rows), f"lista mostra {win.list.count()} cards")
check("norain211/doaxvb-re" not in win.rows, "jogo adulto escondido por padrão")
check(win.b_self.isHidden(), "sem aviso de atualização do launcher quando já está na última versão")
win.settings.set("show_adult", True)
win.reload_entries()
check("norain211/doaxvb-re" in win.rows, "jogo adulto aparece quando a opção é ligada")
win.settings.set("show_adult", False)
win.reload_entries()
win.apply_filters()
first_key = win.list.item(0).data(256)
check(first_key == "banjorecomp/banjorecomp", "jogo com atualização aparece primeiro")
def show(key):
    for i in range(win.list.count()):
        if win.list.item(i).data(256) == key:
            win.list.scrollToItem(win.list.item(i))
    pump(app, 1.5, until=lambda: not win.covers._busy)


for k in ("sonicdcer/mariokart64recomp", "revosucks/bm64recomp", "banjorecomp/banjorecomp"):
    show(k)
pump(app, 0.5, until=lambda: not win.covers._busy)
banjo = win.rows["banjorecomp/banjorecomp"].entry
check(win.covers.pixmap(banjo) is not None, "capa vertical do Banjo carregada")
check(win.covers.composed(banjo, 192, 108).size().width() == 192, "capa vertical encaixada no card")
check(win.covers.pixmap(win.rows["sonicdcer/mariokart64recomp"].entry) is not None, "banner do Mario Kart carregado")
bm = win.rows["revosucks/bm64recomp"].entry
check(win.covers.pixmap(bm) is None and not win.covers.store.needs_fetch(bm.key),
      "jogo sem capa: marcado para não repetir a busca")
check(len(finder.asked) == len(set(finder.asked)), "cada jogo só foi buscado uma vez")
n_asked = len(finder.asked)
win.list.viewport().update()
pump(app, 0.5)
check(len(finder.asked) == n_asked, "repintar não dispara novas buscas")
check(not win.d_cover.isHidden() and win.d_cover.entry.key == first_key, "capa grande no painel de detalhes")
win.grab().save(str(OUT / "01_lista.png"))

# capa própria e rebusca pelo menu
custom = tmp / "minha.png"
custom.write_bytes(make_png(400, 200, "#8a1ea2", "MINHA CAPA"))
win.covers.store.set_custom(bm.key, custom)
win._cover_changed(bm.key)
check(win.covers.pixmap(bm) is not None, "capa definida manualmente aparece")
win.refetch_cover(bm.key)
pump(app, 1.0, until=lambda: not win.covers._busy)
check(win.covers.store.cached(bm.key) is None, "rebuscar limpou a capa e a falha (Bomberman segue sem capa)")

# favoritos / filtros
win.sidebar.setCurrentRow(3)
check(win.list.count() == 1, "filtro Favoritos")
win.sidebar.setCurrentRow(2)
check(win.list.count() == 1, "filtro Atualizações")
win.sidebar.setCurrentRow(0)
win.search.setText("mario kart 64")
check(win.list.count() == 1, "busca por texto")
win.search.setText("")

# instalar de ponta a ponta
key = "zelda64recomp/zelda64recomp"
for i in range(win.list.count()):
    if win.list.item(i).data(256) == key:
        win.list.setCurrentRow(i)
win.render_detail()
check(win.b_main.text() == "Instalar" and win.b_main.isEnabled(), "botão Instalar habilitado")
win.start_install(key)
check(key in win.active, "instalação iniciou")
pump(app, 6, until=lambda: key not in win.active)
rec = lib.get("Zelda64Recomp/Zelda64Recomp")
check(rec is not None and rec["version"] == "v1.0.0", "registro na biblioteca")
check(rec and Path(rec["exe"]).is_file(), f"executável extraído: {rec and rec['exe']}")
check(win.rows[key].installed and win.b_main.text() == "Jogar", "botão virou Jogar")
pump(app, 0.4)
check("Pasta:" in win.d_info.text(), "detalhe mostra a pasta de instalação")
win.grab().save(str(OUT / "02_instalado.png"))

# atualizar o Banjo (nova pasta por versão)
bkey = "banjorecomp/banjorecomp"
for i in range(win.list.count()):
    if win.list.item(i).data(256) == bkey:
        win.list.setCurrentRow(i)
win.start_install(bkey)
pump(app, 6, until=lambda: bkey not in win.active)
brec = lib.get("BanjoRecomp/BanjoRecomp")
check(brec["version"] == "v2.0.0" and len(brec["versions"]) == 2, "atualização mantém a versão antiga lado a lado")
check(not win.rows[bkey].has_update, "selo de atualização sumiu")
win.render_detail()
pump(app, 0.4)
check(not win.b_clean.isHidden(), "botão de limpar versões antigas disponível")
win.grab().save(str(OUT / "04_atualizado.png"))

# tela de descobertos
win.sidebar.setCurrentRow(4)
check(win.list.count() == 1, "filtro Descobertos")
win.grab().save(str(OUT / "03_descobertos.png"))

win.close()
print("\nCapturas em", OUT)
sys.exit(1 if fails else 0)
