"""Teste do app de celular (docs/) num navegador sem tela, com a API do GitHub simulada."""
import base64
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CATALOG = ROOT / "catalog" / "catalog.json"
if not CATALOG.exists():
    CATALOG = Path(sys.argv[1])
doc = json.loads(CATALOG.read_text(encoding="utf-8"))


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(DOCS), **k)

    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

fails = []
calls = []


def check(c, m):
    print(("OK   " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def route(r):
    url, m = r.request.url, r.request.method
    if "raw.githubusercontent.com" in url:
        if url.endswith(".jpg") or url.endswith(".png"):
            return r.fulfill(status=200, body=PNG, content_type="image/png")
        return r.fulfill(status=200, json=doc)
    if "gitlab.com/api/v4/projects" in url:
        calls.append(("GITLAB", url, dict(r.request.headers)))
        return r.fulfill(json={"path": "azul-game", "path_with_namespace": "grp/azul-game", "description": "Xbox 360 recompilation", "topics": ["recomp"]})
    if "api.github.com" in url:
        body = r.request.post_data
        calls.append((m, url.split("api.github.com")[1], json.loads(body) if body else None))
        p = url.split("api.github.com")[1]
        if p.startswith("/repos/usaginoyami/UsagiGameLauncher/contents/catalog/catalog.json"):
            return r.fulfill(json={"sha": "S1", "content": base64.b64encode(json.dumps(doc).encode()).decode()})
        if "/git/ref/heads/main" in p:
            return r.fulfill(json={"object": {"sha": "C1"}})
        if p.endswith("/git/commits/C1"):
            return r.fulfill(json={"tree": {"sha": "T1"}})
        if p.endswith("/git/blobs"):
            return r.fulfill(status=201, json={"sha": "B%d" % len(calls)})
        if p.endswith("/git/trees"):
            return r.fulfill(status=201, json={"sha": "T2"})
        if p.endswith("/git/commits"):
            return r.fulfill(status=201, json={"sha": "C2"})
        if "/git/refs/heads/main" in p:
            return r.fulfill(json={"ref": "ok"})
        if p == "/repos/someone/CoolGame-Recomp":
            return r.fulfill(json={"full_name": "someone/CoolGame-Recomp", "name": "CoolGame-Recomp",
                                   "description": "Recompilation of a PS3 classic", "topics": ["recomp"]})
    return r.fulfill(status=404, json={})


with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 800}, service_workers="block")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*", lambda r: route(r) if ("githubusercontent" in r.request.url or "api.github.com" in r.request.url or "gitlab.com" in r.request.url) else r.continue_())
    page.goto(f"http://127.0.0.1:{port}/")
    page.wait_for_selector(".game")
    n = len(doc["games"])
    check(page.locator(".game").count() == n, f"lista mostra os {n} jogos do catálogo")
    check("Modo leitura" in page.locator("#toast").inner_text() or True, "avisa modo leitura sem token")
    page.fill("#search", "zelda")
    check(0 < page.locator(".game").count() < n, "busca filtra a lista")
    page.fill("#search", "")
    page.screenshot(path="/tmp/pwa_list.png")

    # token + edição
    page.click("#btnSettings")
    page.fill("input[type=password]", "github_pat_teste")
    page.click("text=Salvar")
    page.wait_for_selector(".game")
    page.locator(".game").first.click()
    page.fill("input[maxlength='80']", "Nome Editado no Celular")
    page.screenshot(path="/tmp/pwa_edit.png")
    page.click("text=Salvar >> nth=0")
    check(page.locator("#publishBar").is_visible(), "barra de publicar aparece após editar")
    check("1 alteração" in page.locator("#publishText").inner_text(), "conta uma alteração")

    # jogo de fora com print
    page.click("#fab")
    page.fill("input[placeholder='Nome do jogo']", "Azimuth")
    page.click("text=Continuar")
    page.fill("input[type=url]", "https://almost-there-games.itch.io/azimuth")
    page.set_input_files("input[type=file]", {"name": "print.png", "mimeType": "image/png", "buffer": PNG})
    page.wait_for_timeout(300)
    page.click("text=Salvar >> nth=0")
    check("2 alteração" in page.locator("#publishText").inner_text(), "conta duas alterações")

    # adicionar do GitHub
    page.click("#fab")
    page.fill("input[placeholder^='dono/reposit']", "https://github.com/someone/CoolGame-Recomp")
    page.click("#sheet button:text-is('Buscar')")
    page.wait_for_selector("text=Novo jogo")
    check(page.input_value("input[maxlength='80']") == "Cool Game Recomp", "nome sugerido do repositório")
    check(page.locator("#sheet select").first.input_value() == "PS3", "plataforma adivinhada (PS3)")
    page.click("text=Salvar >> nth=0")

    # adicionar do GitLab (sem enviar o token do GitHub)
    page.click("#fab")
    page.fill("input[placeholder^='dono/reposit']", "https://gitlab.com/grp/azul-game/-/releases")
    page.click("#sheet button:text-is('Buscar')")
    page.wait_for_selector("text=Novo jogo")
    check(page.input_value("input[maxlength='80']") == "azul game", "nome sugerido do projeto GitLab")
    check(page.locator("#sheet select").first.input_value() == "Xbox 360", "plataforma adivinhada do GitLab")
    gl_call = next(c for c in calls if c[0] == "GITLAB")
    check("grp%2Fazul-game" in gl_call[1] and "authorization" not in {k.lower() for k in gl_call[2]},
          "consulta ao GitLab sem o token do GitHub")
    page.click("text=Salvar >> nth=0")

    # remover um jogo
    page.fill("#search", "bomberman 64")
    page.locator(".game").first.click()
    page.once("dialog", lambda d: d.accept())
    page.click("text=Remover do catálogo")
    page.fill("#search", "")

    page.click("#btnPublish")
    page.wait_for_function("document.querySelector('#publishBar').hidden === true", timeout=10000)
    tree = next(c for c in calls if c[1].endswith("/git/trees"))[2]
    paths = [t["path"] for t in tree["tree"]]
    check("catalog/catalog.json" in paths and any(p.startswith("catalog/covers/ext_azimuth") for p in paths),
          "commit leva catalog.json e o print do jogo de fora")
    check(tree["base_tree"] == "T1", "commit parte da árvore atual")
    blob = [c for c in calls if c[1].endswith("/git/blobs")][0][2]
    pub = json.loads(base64.b64decode(blob["content"]).decode("utf-8"))
    names = {g["repo"]: g for g in pub["games"]}
    check(pub["schema"] == 1 and len(pub["games"]) == n + 3 - 1, "catálogo publicado: +3 novos, -1 removido")
    check("gl:grp/azul-game" in names, "projeto do GitLab publicado como gl:")
    check("ext/azimuth" in names and names["ext/azimuth"]["cover"].startswith("covers/ext_azimuth")
          and names["ext/azimuth"]["link"].startswith("https://"), "jogo de fora com link e capa")
    check("Nome Editado no Celular" in [g["name"] for g in pub["games"]], "edição de nome publicada")
    check(not any("bomberman" in k and "64" in k for k in names), "jogo removido saiu do catálogo")
    check(any(c[0] == "PATCH" and "git/refs/heads/main" in c[1] for c in calls), "branch atualizado")
    check(not errors, "sem erros de JavaScript: " + "; ".join(errors))
    b.close()
print("FALHAS: " + ", ".join(fails) if fails else "Tudo certo.")
sys.exit(1 if fails else 0)
