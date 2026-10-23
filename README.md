# Usagi GameLaucher

Launcher para Windows que organiza e lista os **jogos recomp** (recompilações estáticas e ports) publicados no GitHub. Baixa a release mais recente, instala cada versão em pasta própria, avisa de atualizações e abre o jogo.

Inspirado em [teasergames/ports-pc](https://github.com/teasergames/ports-pc) (biblioteca com cards, catálogo e instalação por release) e [SirDiabo/GithubLauncher](https://github.com/SirDiabo/GithubLauncher) (lista de repositórios e atualização via GitHub Releases).

## Como usar

**Rodar pelo código** (precisa do Python 3.10+): dê dois cliques em `run.bat`. Na primeira vez ele cria o ambiente e instala as dependências.

**Gerar o .exe**: execute `build.bat`. O resultado fica em `dist\UsagiGameLauncher\UsagiGameLauncher.exe` (pasta portátil, sem precisar de Python).

## O que ele faz

- **Catálogo curado** (`data/catalog.json`) com os recomps de N64 e Xbox 360 conferidos na lista awesome-unofficial-pc-ports.
- **Descoberta automática**: busca no GitHub pelos tópicos `recomp`, `recompiled`, `recompilation` e `static-recompilation`. Ferramentas (N64Recomp, XenonRecomp etc.), forks e repositórios arquivados são filtrados. Os achados aparecem em **Descobertos**; clique com o botão direito para adicionar ao seu catálogo ou ocultar.
- **Adicionar repositório**: cole `dono/repo` ou o link do GitHub.
- **Instalar / Atualizar**: escolhe sozinho o pacote Windows da release (ignora Linux, macOS, ARM e source), confere o SHA-256 quando o GitHub publica, extrai com proteção contra zip-slip e detecta o executável. Cada versão vai para `Jogo\vX.Y.Z`, então um update nunca sobrescreve a anterior. **Limpar versões antigas** apaga só quando você confirmar.
- **Filtros**: Todos, Biblioteca, Atualizações, Favoritos, Descobertos, mais plataforma e busca por texto.
- **Jogar**: abre o executável na pasta do jogo; se o Windows pedir administrador, o pedido é repassado. O executável pode ser trocado em **Escolher executável**.
- Cache por ETag: atualizar a lista não gasta o limite da API quando nada mudou.

## Configurações

- **Token do GitHub** (opcional): sem permissões especiais, só eleva o limite de 60 para 5.000 requisições por hora. Crie em GitHub → Settings → Developer settings → Personal access tokens.
- **Pasta dos jogos**: padrão `C:\Users\você\Games\UsagiGameLauncher`. Prefira um caminho curto para evitar o limite de 260 caracteres do Windows.
- Pré-releases, tópicos da busca, mínimo de estrelas e minimizar ao abrir o jogo.

Os dados ficam em `%LOCALAPPDATA%\UsagiGameLauncher` (`library.json`, `settings.json`, `catalog.user.json`, `discovered.json`, `launcher.log`). Para um perfil separado: `UsagiGameLauncher.exe --data-dir "D:\Perfil2"`. Use `--offline` para abrir sem consultar o GitHub.

## Editar o catálogo

Adicione jogos em `%LOCALAPPDATA%\UsagiGameLauncher\catalog.user.json` (ou pelo botão da interface). Campos de cada item: `repo`, `name`, `platform`, `description`, `notes` e `asset_hint` (regex opcional para escolher um arquivo específico da release).

```json
{ "version": 1, "games": [
  { "repo": "dono/MeuJogoRecomp", "name": "Meu Jogo", "platform": "N64",
    "asset_hint": "portable" }
] }
```

## Testes

```
python tests/test_core.py        # 31 testes de lógica
python tests/smoke_ui.py pasta   # interface sem tela + instalação ponta a ponta + capturas
```

## Aviso

O launcher **não inclui jogos, ROMs, BIOS nem arquivos de console**. Cada projeto recomp exige a sua própria cópia legítima do jogo, e as instruções de preparo vêm do repositório de cada projeto. O token do GitHub fica salvo em texto no `settings.json` do seu usuário.
