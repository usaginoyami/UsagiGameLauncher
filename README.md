# Usagi GameLaucher

Launcher para Windows que organiza e lista os **jogos recomp** (recompilações estáticas e ports) publicados no GitHub. Baixa a release mais recente, instala cada versão em pasta própria, avisa de atualizações e abre o jogo.

Inspirado em [teasergames/ports-pc](https://github.com/teasergames/ports-pc) (biblioteca com cards, catálogo e instalação por release) e [SirDiabo/GithubLauncher](https://github.com/SirDiabo/GithubLauncher) (lista de repositórios e atualização via GitHub Releases).

## Como usar

**Rodar pelo código** (precisa do Python 3.10+): dê dois cliques em `run.bat`. Na primeira vez ele cria o ambiente e instala as dependências.

**Gerar o .exe**: execute `build.bat`. O resultado fica em `dist\UsagiGameLauncher\UsagiGameLauncher.exe` (pasta portátil, sem precisar de Python).

## O que ele faz

- **Catálogo curado** (`data/catalog.json`) com ~80 recomps e ports (N64, Xbox, Xbox 360, PS1–PS4, PSP, GameCube, Wii, Switch, Dreamcast, SNES, NES, GBA), cada repositório conferido antes de entrar. Sem token do GitHub, o launcher só consulta releases dos jogos instalados, favoritos e do que você abrir (limite de 60 req/h); com token, atualiza tudo.
- **Descoberta automática**: busca no GitHub pelos tópicos `recomp`, `recompiled`, `recompilation` e `static-recompilation`. Ferramentas (N64Recomp, XenonRecomp etc.), forks e repositórios arquivados são filtrados. Os achados aparecem em **Descobertos**; clique com o botão direito para adicionar ao seu catálogo ou ocultar.
- **Adicionar repositório**: cole `dono/repo` ou o link do GitHub.
- **Instalar / Atualizar**: escolhe sozinho o pacote Windows da release (ignora Linux, macOS, ARM e source), confere o SHA-256 quando o GitHub publica, extrai com proteção contra zip-slip e detecta o executável. Cada versão vai para `NomeDoRepositório\vX.Y.Z`, então um update nunca sobrescreve a anterior. **Limpar versões antigas** apaga só quando você confirmar.
- **Renomear**: clique direito no jogo → **Renomear…** (ou F2). O nome é só de exibição, fica salvo nas suas configurações e não muda o repositório, o catálogo nem a pasta do jogo. **Restaurar nome original** desfaz.
- **Nome automático**: clique direito → **Procurar nome automático…** busca o título oficial na IGDB (com a chave) e na Wikipedia, com ano e plataformas, e você escolhe. Os jogos da plataforma do card aparecem primeiro, o que separa, por exemplo, o God of War de PS2 do de PS4.
- **Plataformas**: **Editar plataformas…** define de qual console o jogo foi recompilado e para onde o port foi feito (Windows, Linux, Switch, Android, portátil ARM64…, ou outro texto). Portes que não são para Windows ganham o selo “→ destino” e pedem confirmação antes de baixar. As edições ficam nas suas configurações e podem ser desfeitas voltando ao valor original.
- **Jogo de fora do GitHub** (curador): **Adicionar jogo de fora…** cria um card para jogos que não têm repositório (itch.io, site do projeto). Informe nome, link https, plataforma original, onde roda, descrição e um print (escolha um arquivo ou use **Colar print** com a imagem da área de transferência, ex.: Win+Shift+S). O botão principal abre a página para você baixar; depois use **Já tenho este jogo instalado…** para ligar a pasta e jogar por aqui. Não há atualização automática. No menu de qualquer jogo, **Colar print como capa** troca a capa pela imagem copiada. A publicação do catálogo leva o link e o print, então o cliente também vê.
- **Remover da lista**: clique direito → **Remover da lista** (ou Delete). Não apaga nada do disco; jogo instalado precisa ser desinstalado antes. Para trazer de volta: Configurações → **Restaurar jogos removidos**.
- **Estrelas do GitHub**: cada card mostra a contagem (ex.: ★ 1,2 mil) e o painel mostra o número exato. Ficam em cache por 24 h. Sem token, o launcher só consulta as estrelas dos jogos instalados, favoritos e do que você abrir; com token, atualiza todos.
- **Avisos de atualização**: quando um jogo instalado tem versão nova, aparece uma faixa no topo da lista (“Ver” abre o filtro Atualizações, “Atualizar todos” instala todas) e, com o launcher em segundo plano, um aviso do sistema. Cada versão é avisada uma vez. Enquanto o launcher fica aberto, ele reconfere os jogos instalados a cada 6 horas (mude em Configurações; usa só o cache de ETag, que não gasta o limite da API quando nada mudou).
- **Localizar jogos já instalados**: o botão **Localizar instalados** procura jogos numa pasta que você escolher (até 3 níveis), sugere de qual jogo da lista é cada pasta e deixa você conferir antes de importar. Reconhece o formato `Jogo\vX.Y.Z` do launcher (recupera a versão) e pastas soltas pelo nome. Para um jogo só, use **Já tenho este jogo instalado…** no menu ou no painel. Nada é copiado nem apagado. Jogos importados sem versão conhecida não avisam atualização, mas permitem **Instalar a última release** numa pasta nova.
- **Ordenar**: o seletor “Ordem” ao lado das plataformas organiza por nome (A→Z ou Z→A), estrelas (mais ou menos primeiro), plataforma, release mais recente, jogados recentemente e instalados recentemente, além do padrão (atualizações, instalados e favoritos primeiro). A escolha fica salva. Jogos sem estrelas ou sem release carregados vão para o fim.
- **Filtros**: Todos, Biblioteca, Atualizações, Favoritos, Descobertos, mais plataforma e busca por texto.
- **Jogar**: abre o executável na pasta do jogo; se o Windows pedir administrador, o pedido é repassado. O executável pode ser trocado em **Escolher executável**.
- **Capas**: cada card e o painel de detalhes mostram a capa do jogo, buscada sozinha só dos jogos que aparecem na tela. Ordem das fontes: capa definida no catálogo → IGDB (só com Client ID e Secret da Twitch; filtra pela plataforma do jogo) → SteamGridDB (só com chave) → Wikipedia → primeira imagem do README do repositório. Banner horizontal preenche o card; caixa vertical ganha fundo desfocado. Clique direito no card → **Escolher capa online…** (mostra várias opções da IGDB, SteamGridDB e Wikipedia, com busca por outro nome), **Definir capa de um arquivo…** ou **Buscar capa de novo**. Capa escolhida por você nunca é apagada pela limpeza automática. Quem não tem capa fica com uma letra e só tenta de novo depois de 7 dias; sem internet não conta como falha.
- **Segurança**: antes de baixar um projeto suspeito (achado pela busca automática com poucas estrelas ou criado há pouco, conta com nome gerado, pacote `.exe` solto, extensão dupla, nome de crack/cheat), o launcher mostra os motivos e pede confirmação. “Baixar mesmo assim” lembra a escolha para aquele jogo. Os sinais não provam que algo é seguro: leia o repositório antes de confiar. Dá para desligar nas Configurações.
- **Filtro adulto**: jogos adultos ou sugestivos ficam escondidos do catálogo e da busca automática (o que você adicionou ou instalou nunca some). Ligue em Configurações → “Mostrar jogos adultos”.
- **Backup dos saves**: ao atualizar um jogo, o launcher compacta saves e configurações da versão antiga em `Jogo\_backups` (guarda os 5 últimos) e copia os saves para a pasta da versão nova sem sobrescrever nada. Clique direito no jogo → **Backups dos saves…** para restaurar. Saves que o jogo grava fora da pasta (como `%APPDATA%`) não são mexidos por atualizações.
- **Backup manual**: botão **Backup…** no painel do jogo (ou clique direito → **Fazer backup…**). Você escolhe **só saves e configs** (arquivo pequeno) ou **o jogo inteiro** (toda a pasta da versão instalada, mostra o tamanho antes) e depois onde salvar o `.zip`, por exemplo um HD externo. O arquivo só aparece no destino quando termina (cancelar ou falhar não deixa zip pela metade), o launcher confere o espaço livre e não deixa salvar dentro da pasta do próprio jogo. Em **Backups e restauração…** dá para restaurar o mais recente automático ou escolher um `.zip` qualquer.
- **Atualização do launcher**: avisa na barra de baixo quando há versão nova nas Releases. No `.exe`, baixa, confere o SHA-256, fecha, troca os arquivos e reabre; rodando pelo código, abre a página da release.
- Cache por ETag: atualizar a lista não gasta o limite da API quando nada mudou.
- **Versões e reparo**: botão **Versões, reparo e atalhos ▾** → **Versões instaladas…** deixa voltar a uma versão antiga que ainda está no disco (o “Jogar” passa a abrir ela). **Reparar instalação** baixa de novo o pacote da versão em uso, confere o SHA-256, faz backup de saves e configurações e recoloca os arquivos por cima; saves existentes nunca são sobrescritos, configurações do pacote voltam ao padrão. Nada muda se o download ou o pacote vierem ruins.
- **Atalhos** (Windows): **Criar atalho no desktop** gera um `.lnk` do jogo. **Adicionar à Steam** inclui o jogo como “jogo fora da Steam” em cada conta da Steam do PC; exige a Steam fechada (ela regrava o arquivo ao sair), guarda uma cópia `shortcuts.vdf.usagi-*.bak` do arquivo anterior e não duplica. Jogos abertos por atalho ou pela Steam não contam tempo de jogo aqui.
- **Tempo de jogo, status e nota**: o launcher conta o tempo em que o jogo fica aberto (sessões de menos de 10 s não contam; jogos que o Windows abre com pedido de administrador não são acompanhados). No painel de detalhes: **status** (Quero jogar, Jogando, Terminado, Abandonado; abrir o jogo marca “Jogando” sozinho) e uma **nota** só sua. Há filtros Jogando, Quero jogar e Terminados na barra lateral e as ordens “Tempo de jogo” e “Status”.
- **Segredos no Windows**: o token do GitHub, o Secret da IGDB e a chave do SteamGridDB ficam no Gerenciador de Credenciais do Windows, não no `settings.json`. Quem já tinha o token no arquivo é migrado ao salvar as Configurações. Se o cofre não estiver disponível, volta a guardar no arquivo (as Configurações dizem qual dos dois está valendo).

## Duas edições: curador e cliente

O mesmo código gera dois programas:

- **Curador** (`UsagiGameLauncher.exe`, o seu): descobre projetos, adiciona repositórios, renomeia, edita plataformas e capas, remove da lista. Tudo isso forma o catálogo.
- **Cliente** (`UsagiGameClient.exe`, para quem baixa): não descobre nem adiciona nada. Mostra só o catálogo que você publicou, e instala, joga, atualiza e faz backup como o seu. Tem pasta de dados própria (`%LOCALAPPDATA%\UsagiGameClient`), então os dois convivem no mesmo PC. Se ainda não houve download do catálogo (ou sem internet), usa o catálogo que veio dentro do programa.

**Publicar**: no curador, botão **Publicar catálogo…**. Ele reúne o catálogo do programa mais os seus jogos, sem os removidos e com os seus nomes, plataformas, notas e a marcação de conteúdo adulto (o cliente esconde esses por padrão). Pergunta se inclui as capas que já estão carregadas (as que você escolheu à mão sempre vão). Então:

- **Publicar no GitHub** grava `catalog/catalog.json` e `catalog/covers/` em `usaginoyami/UsagiGameLauncher` (precisa de token com permissão de escrita em *Contents*, nas Configurações; só envia o que mudou).
- **Salvar numa pasta…** só gera os arquivos para você copiar para o repositório e fazer o commit.

O cliente baixa `catalog/catalog.json` pelo `raw.githubusercontent.com` (com ETag, então não gasta nada quando nada mudou), valida o arquivo (formato, `dono/repo`, limites de tamanho, capas só por https) e guarda cópia local. **Atualizar catálogo e releases** força a checagem. Os jogos continuam sendo baixados das releases dos próprios projetos, nunca de você.

**Gerar os dois .exe**: o workflow do GitHub Actions faz os dois e publica na Release `UsagiGameLauncher-Windows-x64.zip` (curador) e `UsagiGameLauncher-Client-Windows-x64.zip` (cliente). Cada edição se atualiza sozinha com o zip da própria edição. Na sua máquina: `build.bat` (curador) e `build-client.bat` (cliente). Para testar o cliente pelo código: `python main.py --client` (use `--data-dir` para não misturar dados).

## Configurações

- **Token do GitHub** (opcional): sem permissões especiais, só eleva o limite de 60 para 5.000 requisições por hora. Crie em GitHub → Settings → Developer settings → Personal access tokens.
- **Pasta dos jogos**: padrão `C:\Users\você\Games\UsagiGameLauncher`. Prefira um caminho curto para evitar o limite de 260 caracteres do Windows.
- Pré-releases, tópicos da busca, mínimo de estrelas e minimizar ao abrir o jogo.
- **Capas**: dá para desligar a busca. Ela envia só o nome do jogo à Wikipedia (e ao SteamGridDB, se você colocar uma chave grátis de steamgriddb.com/profile/preferences/api). A chave vai apenas para a API do SteamGridDB. Para a IGDB (a que mais acha capas de PS4, Xbox 360 e Switch), crie um app grátis em dev.twitch.tv/console (exige 2FA na conta Twitch) e cole o Client ID e o Secret nas Configurações; o Secret só é enviado à Twitch. As imagens pertencem aos seus donos e ficam só no cache local (`covers\`), sem redistribuição.

Os dados ficam em `%LOCALAPPDATA%\UsagiGameLauncher` (`library.json`, `settings.json`, `catalog.user.json`, `discovered.json`, `launcher.log`). Para um perfil separado: `UsagiGameLauncher.exe --data-dir "D:\Perfil2"`. Use `--offline` para abrir sem consultar o GitHub.

## Editar o catálogo

Adicione jogos em `%LOCALAPPDATA%\UsagiGameLauncher\catalog.user.json` (ou pelo botão da interface). Campos de cada item: `repo`, `name`, `platform`, `description`, `notes` e `asset_hint` (regex opcional para escolher um arquivo específico da release), `cover_title` (nome do jogo original, ajuda a achar a capa) e `cover` (link ou arquivo de uma capa sua).

```json
{ "version": 1, "games": [
  { "repo": "dono/MeuJogoRecomp", "name": "Meu Jogo", "platform": "N64",
    "asset_hint": "portable" }
] }
```

## Testes

```
python tests/test_core.py        # 32 testes de lógica
python tests/test_covers.py      # 33 testes das fontes e do cache de capas
python tests/test_extras.py      # 55 testes de segurança, filtro adulto, backups, autoatualização, estrelas, remoção, ordenação e busca de instalados
python tests/test_edition.py     # 40 testes de edições, catálogo publicado, segredos, tempo de jogo, atalhos/Steam, versões e reparo
python tests/smoke_ui.py pasta   # interface sem tela + instalação ponta a ponta + capturas
python tests/smoke_edition.py    # interface sem tela das duas edições (cliente travado, publicar, status, tempo, versões)
```

## Aviso

O launcher **não inclui jogos, ROMs, BIOS nem arquivos de console**. Cada projeto recomp exige a sua própria cópia legítima do jogo, e as instruções de preparo vêm do repositório de cada projeto. No Windows, token e chaves de API ficam no Gerenciador de Credenciais (veja “Segredos no Windows”).
