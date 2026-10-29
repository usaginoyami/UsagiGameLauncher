"""Interface das edições (offscreen): cliente travado, publicar catálogo, status, tempo, versões."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox  # noqa: E402

from usagi_launcher import catalog, edition, paths, published, tracking  # noqa: E402
from usagi_launcher.covers import CoverFinder  # noqa: E402
from usagi_launcher.github_api import GitHubClient  # noqa: E402
from usagi_launcher.jsonio import save_json  # noqa: E402
from usagi_launcher.library import Library  # noqa: E402
from usagi_launcher.settings import Settings  # noqa: E402
from usagi_launcher.ui import theme  # noqa: E402
from usagi_launcher.ui.main_window import MainWindow  # noqa: E402

fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def pump(app, seconds=0.5, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        QCoreApplication.sendPostedEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return until() if until else True


class NoNet(GitHubClient):
    def latest_release(self, *a, **k):
        return None

    def repo_info(self, *a, **k):
        return {"stargazers_count": 1}


app = QApplication([])
app.setStyleSheet(theme.QSS)
tmp = Path(tempfile.mkdtemp())

# ------------------------------------------------------------ edição CLIENTE
edition.set_edition(edition.CLIENT)
paths.set_data_dir(tmp / "client")
published_doc = published.build([
    catalog.Entry("zed/OnlyOne", name="Só Este Jogo", platform="N64", description="curado"),
    catalog.Entry("zed/Spicy", name="Picante", platform="Xbox", adult=True),
])
save_json(paths.data_dir() / "remote_catalog.json", {"etag": "", "ts": 0, "updated": "x", "catalog": published_doc})

settings = Settings(backend=None)
settings.set("install_dir", str(tmp / "games"))
lib = Library()
win = MainWindow(NoNet(""), settings, lib, cover_finder=CoverFinder(enabled=False), auto_refresh=False)
win.show()
pump(app, 0.2)

check(set(win.rows) == {"zed/onlyone"}, "cliente mostra só o catálogo publicado (e esconde o adulto)")
check(not win.btn_discover.isVisible() and not win.btn_add.isVisible() and not win.btn_publish.isVisible(),
      "cliente sem descobrir, adicionar repositório nem publicar")
check(all(f[1] != 4 for f in win.filters), "cliente sem o filtro Descobertos")
check("client" in win.windowTitle().lower(), "título indica a edição cliente")
win.rename_game("zed/onlyone")  # não deve abrir diálogo
win.add_repo()
win.start_discover(manual=True)
check(not win.discovering, "cliente ignora a busca automática")

settings.set("show_adult", True)
win.reload_entries()
check("zed/spicy" in win.rows, "cliente com 'mostrar adultos' vê o marcado como adulto")

# tempo, status e nota (cliente também tem)
lib.record_install("zed/OnlyOne", "Só Este Jogo", "v1", str(tmp / "g/v1"), str(tmp / "g/v1/a.exe"), str(tmp / "g"))
lib.record_install("zed/OnlyOne", "Só Este Jogo", "v2", str(tmp / "g/v2"), str(tmp / "g/v2/a.exe"), str(tmp / "g"))
for v in ("v1", "v2"):
    (tmp / "g" / v).mkdir(parents=True, exist_ok=True)
    (tmp / "g" / v / "a.exe").write_bytes(b"MZ")
win.reload_entries()
win.apply_filters()
win.select("zed/onlyone")
win.set_status("zed/onlyone", "want")
check(win.rows["zed/onlyone"].status == "want", "status 'Quero jogar' aplicado")
check(tracking.status_of(settings, "zed/onlyone") == "want", "status gravado nas configurações")
win.d_mynote.setText("começar pelo tutorial")
win._save_note()
check(tracking.note_of(settings, "zed/onlyone") == "começar pelo tutorial", "nota pessoal gravada")
win.sidebar.setCurrentRow([f[1] for f in win.filters].index(6))
pump(app, 0.2)
check(win.list.count() == 1, "filtro 'Quero jogar' lista o jogo")
win.sidebar.setCurrentRow(0)

win._credit_play("zed/OnlyOne", 5400)
check(lib.get("zed/OnlyOne")["playtime"] == 5400, "tempo de jogo creditado na biblioteca")
win.render_detail()
check("1 h 30 min" in win.d_info.text(), "detalhe mostra o tempo de jogo")

# jogar: abre, acompanha e marca como Jogando
class P:
    def wait(self):
        time.sleep(0.05)


from usagi_launcher.ui import main_window as mw  # noqa: E402
orig_launch = mw.launcher.launch
mw.launcher.launch = lambda exe, args="": P()
win.play("zed/onlyone")
check(win.rows["zed/onlyone"].status == "playing", "abrir o jogo marca 'Jogando'")
mw.launcher.launch = orig_launch
pump(app, 0.3)

# versões
QInputDialog.getItem = staticmethod(lambda *a, **k: ("v1", True))
win.choose_version("zed/onlyone")
check(lib.get("zed/OnlyOne")["version"] == "v1", "voltou para a versão anterior")

# menu do cliente não oferece curadoria
captured = {}


class RecordingMenu(mw.QMenu):
    def exec(self, *a, **k):
        captured["texts"] = [x.text() for x in self.actions()]


real_menu = mw.QMenu
mw.QMenu = RecordingMenu
item = win.list.item(0)
win.item_menu(win.list.visualItemRect(item).center())
mw.QMenu = real_menu
texts = " | ".join(captured.get("texts", []))
check("Editar plataformas" not in texts and "Renomear" not in texts and "Escolher capa" not in texts,
      "menu do cliente sem renomear, plataformas nem capa")
check("Status" in texts and "Remover da lista" in texts, "menu do cliente mantém status e remover")
win.close()

# ------------------------------------------------------------ edição CURADOR
edition.set_edition(edition.CURATOR)
paths.set_data_dir(tmp / "curator")
settings = Settings(backend=None)
settings.set("github_token", "")
lib = Library()
catalog.save_user([catalog.Entry("me/MyGame", name="Meu Jogo", platform="PS2", source="user")])
settings.set("custom_names", {"me/mygame": "Meu Jogo Editado"})
win = MainWindow(NoNet(""), settings, lib, cover_finder=CoverFinder(enabled=False), auto_refresh=False)
win.show()
pump(app, 0.2)
check(win.btn_publish.isVisible() and win.btn_discover.isVisible(), "curador vê descobrir e publicar")

curated = {e.key: e for e in win._curated_entries()}
check("me/mygame" in curated and curated["me/mygame"].name == "Meu Jogo Editado",
      "publicação usa seus jogos e seus nomes")
check(len(curated) >= 79, "publicação inclui o catálogo do programa")
check(not any(e.source == "discovered" for e in curated.values()), "descobertos não são publicados")

# capas: uma escolhida por você sempre vai; automáticas só se aceitar

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402


def png(color):
    img = QImage(60, 80, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


win.covers.store.set_custom_bytes("me/mygame", png("#aa0000"))
win.covers.store._write("zed/auto", png("#00aa00"))
out_dir = tmp / "out"
out_dir.mkdir()
state = {"buttons": []}


class FakeBox(mw.QMessageBox):
    pass


answers = iter([QMessageBox.StandardButton.No])  # sem capas automáticas
mw.QMessageBox.question = staticmethod(lambda *a, **k: next(answers))
orig_box_exec = QMessageBox.exec


def pick_folder_button(self):
    for b in self.buttons():
        if "pasta" in b.text():
            self._chosen = b
            return 0
    return 0


QMessageBox.exec = pick_folder_button
QMessageBox.clickedButton = lambda self: getattr(self, "_chosen", None)
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(out_dir))
win.publish_catalog()
doc = json.loads((out_dir / "catalog" / "catalog.json").read_text(encoding="utf-8"))
games = {g["repo"].lower(): g for g in doc["games"]}
check(doc["schema"] == 1 and "me/mygame" in games, "catalog.json gravado com o seu jogo")
check(games["me/mygame"].get("cover", "").startswith("covers/"), "capa escolhida por você foi publicada")
check((out_dir / "catalog" / games["me/mygame"]["cover"]).is_file(), "arquivo da capa existe na pasta")
check(not any(g.get("cover") for r, g in games.items() if r != "me/mygame"),
      "capas automáticas ficaram de fora quando você recusou")
roundtrip = published.parse(doc)
check(len(roundtrip) == len(doc["games"]), "o cliente consegue ler o que o curador publicou")
QMessageBox.exec = orig_box_exec

# jogo de fora do GitHub (itch.io) com print colado
from usagi_launcher.ui import edit_dialogs as ed  # noqa: E402


class FakeDlg:
    cover = png("#3355aa")

    def __init__(self, parent=None):
        pass

    def exec(self):
        return mw.QDialog.DialogCode.Accepted

    def values(self):
        return {"name": "Azimuth", "link": "https://almost-there-games.itch.io/azimuth",
                "platform": "Xbox One", "target": "Windows", "description": "teste"}


mw.ExternalGameDialog = FakeDlg
win.add_external()
ext = win.rows.get("ext/azimuth")
check(ext is not None and ext.entry.external, "jogo de fora adicionado ao catálogo do curador")
check(any(u.key == "ext/azimuth" for u in catalog.load_user()), "jogo de fora gravado no catálogo do usuário")
check(win.covers.store.image_for("ext/azimuth")[1], "print ficou como capa escolhida")
win.select("ext/azimuth")
check(win.b_main.text() == "Abrir página do jogo" and win.b_main.isEnabled(), "botão principal abre a página")
check("itch.io" in win.d_repo.text(), "painel mostra o link no lugar do repositório")
check(ext.loaded and ext.release is None, "sem consulta de releases para jogo de fora")
opened = []
mw.QDesktopServices.openUrl = staticmethod(lambda u: opened.append(u.toString()))
win.primary_action("ext/azimuth")
check(opened == ["https://almost-there-games.itch.io/azimuth"], "abre a página no navegador")
QMessageBox.exec = pick_folder_button
QMessageBox.clickedButton = lambda self: getattr(self, "_chosen", None)
answers = iter([QMessageBox.StandardButton.Yes])
mw.QMessageBox.question = staticmethod(lambda *a, **k: next(answers))
out2 = tmp / "out2"
out2.mkdir()
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(out2))
win.publish_catalog()
doc2 = json.loads((out2 / "catalog" / "catalog.json").read_text(encoding="utf-8"))
g = {x["repo"]: x for x in doc2["games"]}["ext/azimuth"]
check(g["link"].startswith("https://") and g["cover"].startswith("covers/"), "publicação leva o link e o print do jogo de fora")
QMessageBox.exec = orig_box_exec

win.close()
edition.set_edition(None)
print()
print("FALHAS:" if fails else "Tudo certo.", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
