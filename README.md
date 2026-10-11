# todonow

Gerenciador de tarefas **local**, feito para rodar no seu homelab em Docker.
Flask + Bootstrap 5 + SQLite, com login local por usuário e senha, visão de
calendário, diário no estilo **BUJO (bullet journal)**, backup e restauração em JSON.

```
• tarefa   ○ evento   – nota   ✕ concluída   * prioridade
```

---

## Índice

- [Recursos](#recursos)
- [Como rodar (Docker)](#como-rodar-docker)
- [Primeiro acesso](#primeiro-acesso)
- [As telas do app](#as-telas-do-app)
- [Backup e restauração](#backup-e-restauração)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Consumo de recursos](#consumo-de-recursos)
- [Desenvolvimento local](#desenvolvimento-local)
- [Testes](#testes)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Segurança](#segurança)
- [Solução de problemas](#solução-de-problemas)

---

## Recursos

| Requisito | Como foi implementado |
|---|---|
| Roda em Flask | Flask 3 com *application factory*, blueprints (`auth`, `tasks`, `main`, `backup`) |
| Login local (usuário e senha) | Flask-Login + `werkzeug.security` (scrypt). Sessão com cookie HttpOnly/SameSite |
| Bootstrap | Bootstrap 5.3 **vendorizado** em `app/static/vendor` (funciona sem internet) |
| Ícones | Bootstrap Icons 1.11 vendorizado (fonte `woff2` local) |
| Docker e docker-compose | `Dockerfile` (python:3.13-slim, usuário não-root) + `docker-compose.yml` com healthcheck e limites |
| Adicionar tarefas e riscar ao concluir | Formulário rápido no topo + **alvo circular** à esquerda da linha (com anel, realce no hover, anel de foco e dica na seção) que risca com `line-through`; contadores, barra e seções Hoje/Concluídas se atualizam na hora, **sem F5** |
| Editar e remover | Cada tarefa é única: editar, migrar ou remover fica na própria linha. Ações destrutivas pedem confirmação em **modal Bootstrap** (claro/escuro), com o texto do que será apagado |
| Cada usuário tem seu perfil e suas tarefas | `user_id` em toda tarefa; toda consulta é filtrada pelo usuário logado (404 para tarefas de terceiros) |
| Calendário | FullCalendar 6 (vendorizado, locale pt-BR): mês, semana e lista; **arraste para reagendar** |
| A tarefa começa no dia atual | `scheduled_date` tem `default=today` no modelo e é pré-preenchida com hoje |
| Criar tarefas para o futuro | Campo "Começa em" com atalhos (+1, +7, +30 dias) e o **Future Log** agrupado por mês |
| Estilo BUJO | Daily Log, Monthly Log e Future Log, símbolos *rapid logging*, prioridades `!`/`*` e **adiamento/migração** (a entrada rola para o novo dia sem criar cópia, sem virar "concluída" e sem gerar gêmea no destino) |
| Anotações por tarefa | **Dois modos de escrita**: *Visual* (WYSIWYG, para quem não escreve Markdown) e *Markdown* (sintaxe pura). Guardado como Markdown, renderizado no estilo BUJO — item concluído **riscado**, não checkbox. Editável no formulário e direto na tela de detalhe; a lista mostra um selo e um resumo renderizado |
| Backup e restauração | Exportar/importar JSON (baixar, colar ou enviar arquivo), snapshots no servidor com rotação e restauração nos modos *mesclar*/*substituir*, além de comandos de CLI |
| Começa limpo para quem baixar | A imagem não leva banco, usuário nem tarefa: o primeiro boot cria um banco vazio e o cadastro fica aberto para você criar a sua conta |
| Celular e desktop | Layout responsivo (testado de 360px a 1440px, sem rolagem horizontal), alvos de toque de ~44px, sem zoom involuntário no iOS, e **instalável na tela inicial** (manifest + ícone) |
| Acesso remoto | Pronto para **Tailscale**/proxy reverso: `TRUST_PROXY` respeita `X-Forwarded-Proto`, `TODONOW_BIND` publica só na interface do tailnet e `TRUSTED_HOSTS` limita os hosts aceitos |
| Rodar com menos de 4 GB | 2 workers gthread, SQLite em WAL; limite padrão de **512 MB** no compose (na prática, ~90 MB ociosos) |

Extras: tema claro/escuro, prazos (*deadline* separado do início),
busca e filtros, estatísticas, administração de usuários,
`/healthz` para monitoramento e suporte a migrações Alembic.

---

## Como rodar (Docker)

### 1. Prepare o ambiente

```bash
git clone <seu-repo> todonow && cd todonow
cp .env.example .env
```

Edite o `.env` e, no mínimo, gere uma `SECRET_KEY`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Suba o container

A imagem já está publicada no Docker Hub — não precisa construir nada:

```bash
docker compose up -d     # confere a latest no Docker Hub, baixa se mudou e sobe
docker compose logs -f
```

Acesse **http://localhost:8081** (ou `http://IP-DO-HOMELAB:8081`).

> A porta do host é a `TODONOW_PORT` (padrão **8081**, escolhida por ser pouco
> disputada em homelab). Para trocar: mude no `.env` e rode
> `docker compose up -d`. A porta interna também é 8081 (`TODONOW_CONTAINER_PORT`).

Com o Makefile fica ainda mais curto:

```bash
make up      # confere a latest e sobe
make logs    # acompanhar
make ps      # status e consumo de memória
make down    # parar
```

### Qual imagem está sendo usada

Por padrão o compose usa **`nickolback/todonow:latest`** com
`pull_policy: always`: **todo `docker compose up -d` confere se saiu versão nova**
e recria o container quando a imagem mudou — você atualiza só rodando `make up`.

```dotenv
# Padrão: acompanha a latest
TODONOW_IMAGE=nickolback/todonow:latest
TODONOW_PULL_POLICY=always

# Para fixar uma versão (reproduzível):
# TODONOW_IMAGE=nickolback/todonow:1.1.0

# Para nunca consultar o registry no `up` (atualiza só quando você pedir):
# TODONOW_PULL_POLICY=missing
```

> Como a tag `latest` se move, esse `up` precisa de rede. Se o seu homelab fica
> offline às vezes, troque o padrão para `TODONOW_PULL_POLICY=missing`: aí o app
> sobe com a imagem que já está no host, e a atualização acontece quando você
> rodar `docker compose pull && docker compose up -d`.

Para saber **qual versão está rodando**: o rodapé do app e o `/healthz` mostram
a versão (`v1.1.0`), e `docker compose images` mostra o id da imagem.

> **Arquitetura:** a imagem publicada é **amd64**. Se o seu homelab for ARM
> (Raspberry Pi, por exemplo), construa a sua com
> `make build` (ou publique um manifesto multi-arquitetura, veja abaixo).

### Construindo da fonte

```bash
make build       # constrói a imagem local com a tag do TODONOW_IMAGE
make up-build    # constrói e sobe (ignora a imagem publicada)

# equivalente sem Makefile:
docker compose -f docker-compose.yml -f docker-compose.build.yml build
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

O `docker-compose.yml` tem só a imagem de produção; o `build:` fica no
`docker-compose.build.yml`, para o `up` do dia a dia nunca construir por engano.

### Publicando uma nova versão no Docker Hub

```bash
make push                        # constrói e publica a latest
make release VERSION=1.1.0       # publica a latest + uma tag fixa de versão

# multi-arquitetura (amd64 + arm64) direto com buildx:
docker buildx build --platform linux/amd64,linux/arm64 \
  -t nickolback/todonow:latest -t nickolback/todonow:1.1.0 --push .
```

> A tag `latest` é a que o compose do homelab segue: depois de `make push`, o
> próximo `make up` (ou `docker compose up -d`) já pega a versão nova.

### 3. Onde ficam os dados

Tudo vive no volume Docker nomeado `todonow-data`, montado em `/data`:

```
/data/todonow.db      # banco SQLite (modo WAL)
/data/backups/        # snapshots JSON
/data/secret_key      # chave de sessão gerada automaticamente
```

Para publicar em outra porta, ajuste `TODONOW_PORT` no `.env`.

---

## Primeiro acesso

A tela de login tem **duas abas: *Entrar* e *Criar conta*** — quem ainda não tem
conta se cadastra ali mesmo, informando usuário, nome, e-mail (opcional) e senha.

Há três formas de criar a primeira conta:

**a) Pela própria tela de login (mais simples).** Aba *Criar conta* → preencha e
envie. O primeiro usuário do servidor vira **administrador** automaticamente.

**b) Automático via `.env`.** Preencha as variáveis abaixo antes de subir; o
container cria (ou garante) o admin no start, sem sobrescrever uma conta que já
existe:

```dotenv
TODONOW_ADMIN_USER=admin
TODONOW_ADMIN_PASSWORD=uma-senha-boa-aqui
TODONOW_ADMIN_EMAIL=admin@homelab.local
```

> Depois do primeiro boot você pode apagar essas três linhas do `.env`.

**c) Pelo CLI:**

```bash
# criar/atualizar um usuário
docker compose exec todonow flask --app wsgi create-user maria --password 'senha-forte' --admin

# popular uma conta com tarefas de exemplo
docker compose exec todonow flask --app wsgi seed-demo maria

# listar contas
docker compose exec todonow flask --app wsgi list-users

# recuperar acesso (esqueceu a senha?)
docker compose exec todonow flask --app wsgi set-password maria
```

### Sua instância começa limpa

A imagem e o repositório **não levam dados**: nenhum usuário, nenhuma tarefa,
nenhum banco e nenhum segredo. Ao subir num host novo, o app cria o banco vazio
no volume e a tela de login oferece *Criar conta* — a primeira conta criada vira
administradora (e você pode desligar o autocadastro depois com
`ALLOW_REGISTRATION=no`).

O volume guarda apenas o que o próprio app gera em execução:

```
/data/todonow.db      banco criado no primeiro boot (vazio: 0 usuários, 0 tarefas)
/data/secret_key      chave de sessão gerada na hora
/data/backups/        snapshots, quando você criar algum
```

Para **zerar tudo** e voltar ao estado de fábrica (apaga usuários e tarefas):

```bash
docker compose down -v     # remove o container E o volume de dados
docker compose up -d       # sobe de novo, banco vazio
```

> Se você só quer limpar as tarefas de um usuário, entre como admin e use
> *Excluir concluídas/canceladas*, ou o comando
> `flask --app wsgi dedupe-tasks` para duplicatas.

### Quem pode se cadastrar?

A variável `ALLOW_REGISTRATION` controla a aba *Criar conta*:

| Valor | Comportamento |
|---|---|
| `yes` | A aba fica sempre aberta — qualquer pessoa com acesso à rede cria a própria conta |
| `auto` (padrão do exemplo) | Aberta só enquanto não existe nenhum usuário; depois a aba aparece **desabilitada** com um aviso |
| `no` | Sempre desabilitada; só o admin cria contas em **Usuários** |

A aba fica **sempre visível** na tela de login; quando o cadastro está fechado
ela aparece desabilitada explicando que o administrador deve criar a conta.

> Rodando em rede doméstica, `ALLOW_REGISTRATION=yes` é o mais prático. Se for
> expor o app para fora da LAN, prefira `no` e crie as contas pelo admin.


---

## Acesso remoto com Tailscale

O app roda em HTTP na porta publicada; quem cuida do transporte é o Tailscale.
Há dois jeitos, e a escolha muda **uma** configuração:

### Opção A — IP do tailnet (HTTP, mais simples)

Acesse direto `http://100.x.y.z:8081` (ou `http://nome-da-maquina:8081`) de
qualquer dispositivo da tailnet. Nada a configurar além do Tailscale instalado.

Para não expor a porta na LAN, publique só na interface do tailnet no `.env`:

```dotenv
TODONOW_BIND=100.x.y.z     # o IP do host no tailnet
TODONOW_PORT=8081
```

### Opção B — Tailscale Serve (HTTPS com nome e certificado)

```bash
tailscale serve --bg 8081        # publica https://nome.tailnet.ts.net -> localhost:8081
```

Aí o TLS termina no Tailscale, então diga isso ao app no `.env`:

```dotenv
TRUST_PROXY=true                         # respeita X-Forwarded-Proto/Host
SESSION_COOKIE_SECURE=true               # cookie só trafega em HTTPS
TRUSTED_HOSTS=nome.tailnet.ts.net        # opcional: recusa outros Host
TODONOW_BIND=127.0.0.1                   # a porta só precisa ser local
```

Reinicie com `docker compose up -d` depois de mudar o `.env`.

> `TRUSTED_HOSTS` é uma trava extra: sem ela o app responde a qualquer `Host`
> header. Com ela, um Host desconhecido recebe 400 em vez de servir a aplicação.

## No celular e no desktop

A interface é a mesma para os dois — Bootstrap 5 com ajustes próprios para
telas pequenas:

- **Sem rolagem horizontal** de 360px (Android pequeno) a 1440px (desktop) —
  as tabelas largas (Mensal) rolam dentro do próprio card.
- **Alvos de toque de ~44px** nas ações da linha (concluir, editar, adiar,
  remover) e no menu; a barra de filtros e o editor se reorganizam em blocos.
- **Sem zoom involuntário no iOS**: no celular os campos usam 16px.
- O menu vira *hambúrguer*, e o calendário quebra a barra de ferramentas em
  linhas em vez de espremer os botões.

### Instalar como app na tela inicial

Há um manifest e um ícone, então dá para abrir em tela cheia, sem barra de
navegador:

- **Android/Chrome:** menu ⋮ → *Adicionar à tela inicial* / *Instalar app*.
- **iPhone/Safari:** botão *Compartilhar* → *Adicionar à Tela de Início*.

## As telas do app

| Rota | O que faz |
|---|---|
| `/` | **Hoje**: formulário rápido de adição, tarefas do dia, pendências de dias anteriores, concluídas de hoje e prévia dos próximos 7 dias |
| `/day/<data>` | **Daily Log (BUJO)**: entradas do dia, legenda dos símbolos e migração das pendências |
| `/monthly` | **Monthly Log**: o mês em formato de tabela, com atalhos para cada dia |
| `/future` | **Future Log**: tudo o que está agendado, agrupado por mês |
| `/calendar` | **Calendário** (mês/semana/lista), filtros por situação e tipo, arrastar para reagendar |
| `/tasks/` | Lista completa com busca, filtros e paginação |
| `/tasks/new`, `/tasks/<id>/edit`, `/tasks/<id>` | Criar, editar e ver detalhes |
| `/backup/` | Backup, restauração e snapshots |
| `/auth/profile` | Perfil: nome, e-mail, tema, troca de senha e estatísticas |
| `/auth/users` | Admin: criar, ativar/desativar, promover e excluir usuários |
| `/healthz` | Health check em JSON (usado pelo Docker) |

### Como a migração funciona

Adiar (= migrar) é **rolar a entrada para outro dia**: a mesma tarefa muda de
data e o número de rolagens fica registrado (`migration_count`). Nada é copiado e
nada é marcado como concluído — quem foi para amanhã continua pendente, agora em
"Próximos 7 dias" e no diário do dia novo.

Os atalhos do botão **Adiar** são **relativos à data da própria tarefa**
(+1 dia / +7 dias / escolher data, sempre no futuro), então não existe
"adiar para o dia em que ela já está". E, para não criar duplicidade, adiar para
um dia que **já tem uma tarefa aberta com o mesmo nome** é recusado com um aviso —
nada é alterado.

> Versões antigas criavam uma **cópia** a cada migração e marcavam a original
> como `migrated`, o que gerava duplicidade e fazia a tarefa aparecer como
> concluída. A migração de banco converte esses registros e o comando
> `flask dedupe-tasks` remove as cópias que sobraram (rode com backup antes).

### Anotações das atividades

Cada tarefa tem um diário curto do que aconteceu, com **duas formas de escrever**:

- **Visual** (padrão) — WYSIWYG: você formata com os botões e vê o resultado na
  hora, sem saber Markdown. É o modo para quem não quer lidar com sintaxe.
- **Markdown** — o texto puro, para quem prefere digitar `**negrito**` e listas.
  Trocar de modo converte o conteúdo nos dois sentidos.

O que fica salvo é sempre **Markdown**; o editor converte o HTML do modo visual
com o Turndown (vendorizado, funciona offline) e o servidor renderiza de volta
com o mesmo pipeline da leitura.

Os itens de atividade seguem o **estilo BUJO** — símbolo + riscado, sem checkbox
de formulário:

```markdown
## Diário da migração

- [ ] copiar 2 TB             ->  •  copiar 2 TB
- [x] comprar HD de 4 TB      ->  ✕  comprar HD de 4 TB  (riscado)
- [-] comprar fita LTO        ->  ~  comprar fita LTO    (riscado e apagado)
- [>] rolou para amanhã       ->  >
- [<] agendar em novembro     ->  <
- [o] janela de manutenção    ->  ○
```

A barra tem um botão para cada marcador; no modo Visual ele já insere o item
pronto (basta digitar o texto).

- O HTML é sanitizado (allowlist) antes de aparecer: nada que você cole de fora
  consegue injetar script.
- Onde editar: **formulário da tarefa** (criar/editar) e **tela de detalhe**
  (cartão *Anotações*, com o texto renderizado e o botão *Editar*/*Escrever*).
- A lista de tarefas mostra um selo *anotações* e o resumo já renderizado
  (com os símbolos e o riscado), não o Markdown cru.
- As anotações entram no backup/restauração como texto Markdown.

### Fluxo BUJO do dia a dia

1. **Manhã** — abra *Hoje*, registre as tarefas no campo rápido (elas nascem com a data de hoje).
2. **Durante o dia** — clique no símbolo `•` para concluir: a entrada fica riscada `✕`.
3. **Não terminou?** — use o botão ↻ e *Migrar*: a **mesma** tarefa passa para amanhã (ou +7 dias / uma data escolhida). Migrar não é concluir e não cria cópia — o contador de rolagens fica no histórico da entrada.
4. **Planejou algo distante?** — agende com data futura: vai para o *Future Log* e aparece no calendário.
5. **Mês novo** — abra o *Monthly Log* e migre o que estiver no *Future Log*.

---

## Backup e restauração

### Pela interface (`/backup/`)

- **Exportar** — *Minhas tarefas* (padrão) ou *Instância completa* (admin, inclui
  todos os usuários **com os hashes de senha**, para preservar os logins).
- **Restaurar** — envie o `.json` **ou** cole o conteúdo, escolhendo o modo:
  - **Mesclar** (recomendado): atualiza pelo `uuid` e adiciona o que falta.
    Restaurar duas vezes o mesmo arquivo não duplica nada.
  - **Substituir**: apaga as tarefas atuais antes de importar.
- **Snapshots no servidor** — grava o JSON em `/data/backups`, mantém os últimos
  `BACKUP_KEEP` (20 por padrão) e permite baixar, restaurar (mesclar/substituir) ou excluir.

### Pela linha de comando

```bash
# backup completo da instância
docker compose exec todonow flask --app wsgi backup --scope full

# backup de um usuário
docker compose exec todonow flask --app wsgi backup --scope user --username maria

# gravar em um caminho específico
docker compose exec todonow flask --app wsgi backup --scope full --output /data/backups/pre-upgrade.json

# restaurar (mesclar) um arquivo que está dentro do container
docker compose exec todonow flask --app wsgi restore /data/backups/pre-upgrade.json --mode merge

# listar snapshots
docker compose exec todonow flask --app wsgi snapshots

# remover tarefas duplicadas (mesmo título, data e situação)
docker compose exec todonow flask --app wsgi dedupe-tasks          # só mostra o plano
docker compose exec todonow flask --app wsgi dedupe-tasks --apply  # remove de fato
# ...e também os artefatos que ficaram em dias diferentes (par ligado pela
# migração antiga, ou uma entrada concluída atrás de outra aberta com o mesmo nome)
docker compose exec todonow flask --app wsgi dedupe-tasks --across-days --apply

# backup do volume para o host (opção mais crua)
make backup
```

### Sugestão de cron no homelab

```cron
# todo dia às 3h da manhã
0 3 * * * cd /srv/todonow && docker compose exec -T todonow flask --app wsgi backup --scope full >> /var/log/todonow-backup.log 2>&1
```

Formato do arquivo:

```json
{
  "format": "todonow.backup",
  "format_version": 1,
  "app": "todonow",
  "app_version": "1.0.0",
  "scope": "user",
  "exported_at": "2025-10-10T18:02:11",
  "counts": { "tasks": 42, "users": 1 },
  "user": { "username": "maria", "...": "..." },
  "tasks": [
    {
      "uuid": "9f1c…", "title": "Revisar backup", "kind": "task",
      "status": "open", "priority": "high",
      "scheduled_date": "2025-10-10", "due_date": "2025-10-12",
      "notes": null, "completed_at": null, "position": 1
    }
  ]
}
```

---

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `SECRET_KEY` | gerado em `/data/secret_key` | Chave de assinatura das sessões. **Defina em produção.** |
| `TODONOW_ADMIN_USER` / `_PASSWORD` / `_EMAIL` | vazio | Cria o admin no start do container (sem sobrescrever existentes) |
| `ALLOW_REGISTRATION` | `auto` | Controla a aba *Criar conta* na tela de login: `yes` (sempre aberta), `auto` (só sem usuários) ou `no` |
| `FIRST_USER_IS_ADMIN` | `true` | Primeira conta criada vira admin |
| `MIN_PASSWORD_LENGTH` | `8` | Tamanho mínimo de senha |
| `TODONOW_IMAGE` | `nickolback/todonow:latest` | Imagem usada pelo compose |
| `TODONOW_PULL_POLICY` | `always` | `always` confere a `latest` a cada `up`; `missing` usa a imagem local |
| `TODONOW_PORT` | `8081` | Porta publicada no host |
| `TODONOW_CONTAINER_PORT` | `8081` | Porta interna do container (gunicorn) |
| `TODONOW_BIND` | `0.0.0.0` | Interface onde publicar (`127.0.0.1`, IP do tailnet, …) |
| `TRUST_PROXY` | `false` | Confia em `X-Forwarded-Proto/Host` (Tailscale Serve, Caddy, Nginx) |
| `TRUST_PROXY_HOPS` | `1` | Quantos proxies confiar |
| `TRUSTED_HOSTS` | vazio | Hosts aceitos (separados por vírgula); vazio = qualquer um |
| `SESSION_COOKIE_SECURE` | `false` | Ligue se servir por HTTPS (obrigatório com Tailscale Serve) |
| `APP_TIMEZONE` | `America/Sao_Paulo` | Fuso usado em "hoje" e nos horários |
| `WEEK_START` | `6` | `0`=segunda … `6`=domingo |
| `DEFAULT_THEME` | `light` | Tema inicial |
| `GUNICORN_WORKERS` / `_THREADS` | `2` / `4` | Concorrência |
| `TASKS_PER_PAGE` | `50` | Paginação da lista |
| `BACKUP_DIR` | `/data/backups` | Onde os snapshots são gravados |
| `BACKUP_KEEP` | `20` | Quantos snapshots manter por escopo |
| `ALLOW_FULL_RESTORE` | `true` | Permite restauração completa por admin |
| `TODONOW_MEM_LIMIT` | `512m` | Limite de memória do container |
| `TODONOW_CPUS` | `1.0` | Limite de CPU |

---

## Consumo de recursos

O requisito de rodar abaixo de 4 GB é atendido com folga:

- imagem `python:3.13-slim` (sem compiladores, sem servidor de banco);
- SQLite em arquivo único com WAL — sem processo extra de banco;
- gunicorn com **2 workers gthread** e `--max-requests 2000` para conter vazamentos;
- assets estáticos servidos pelo próprio Flask (app de uso pessoal);
- limites explícitos no compose: `mem_limit: 512m`, `cpus: 1.0`, `pids_limit: 256`.

Para conferir no seu servidor:

```bash
docker stats --no-stream todonow
```

Se você tiver poucos usuários, dá para reduzir ainda mais com
`GUNICORN_WORKERS=1` no `.env` (na casa dos 70 MB de RSS).

---

## Desenvolvimento local

```bash
make install     # cria .venv e instala requirements-dev.txt
make run         # Flask em modo debug em http://127.0.0.1:8081 (local, sem Docker)
make test        # pytest
make lint        # ruff
make migrate MSG="nova coluna"
```

Alternativa com Docker e recarregamento automático:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

---

## Testes

```bash
make test
# ou
.venv/bin/pytest -q
.venv/bin/pytest --cov=app --cov-report=term-missing
```

A suíte cobre autenticação e isolamento entre usuários, CRUD e conclusão de
tarefas, migração BUJO (sem duplicar), logs diário/mensal/futuro, feed e reagendamento do
calendário, backup/restauração (mesclar, substituir, snapshots, tentativa de
path traversal) e comandos de CLI.

---

## Estrutura do projeto

```
todonow/
├── app/
│   ├── __init__.py          # application factory, healthcheck, headers, PRAGMAs SQLite
│   ├── config.py            # configuração por ambiente (env vars)
│   ├── extensions.py        # db, migrate, login_manager, csrf
│   ├── models.py            # User, Task (+ regras BUJO: complete, migrate_to, consultas)
│   ├── utils.py             # datas em pt-BR, símbolos BUJO, matriz do mês
│   ├── filters.py           # filtros Jinja e variáveis globais dos templates
│   ├── security.py          # admin_required, política de cadastro, redirect seguro
│   ├── cli.py               # init-db, create-user, backup, restore, snapshots, seed-demo…
│   ├── auth/                # login, registro, perfil, administração de usuários
│   ├── tasks/               # CRUD, concluir, migrar (mover de dia), reagendar
│   ├── main/                # dashboard, daily/monthly/future log, calendário e feed JSON
│   ├── backup/              # exportar, importar, snapshots (service.py + rotas)
│   ├── templates/           # Jinja (base, macros BUJO, telas por blueprint)
│   └── static/              # css/js próprios + Bootstrap, Icons e FullCalendar vendorizados
├── migrations/              # Alembic (flask db upgrade no start do container)
├── tests/                   # pytest (86 testes)
├── docker/entrypoint.sh     # migra + bootstrap do admin + gunicorn
├── Dockerfile               # imagem enxuta, usuário não-root, healthcheck
├── docker-compose.yml       # produção no homelab (limites de memória/CPU)
├── docker-compose.dev.yml   # override de desenvolvimento com reload
├── Makefile                 # atalhos (up, logs, backup, test…)
└── .env.example             # todas as variáveis documentadas
```

### Modelo de dados (resumo)

**users** — `id`, `username` (único), `email`, `display_name`, `password_hash`,
`is_admin`, `is_active`, `theme`, `last_login_at`.

**tasks** — `id`, `uuid` (usado no backup), `user_id`, `title`, `notes`,
`kind` (`task`/`event`/`note`), `status` (`open`/`done`/`migrated`/`cancelled`),
`priority`, `scheduled_date` (início, padrão hoje), `due_date` (prazo opcional),
`completed_at`, `migrated_to_date`, `migrated_from_id`, `position`.
A coluna `collection` continua no banco (e no backup) por compatibilidade com
instâncias antigas, mas não aparece mais na interface.

---

## Segurança

- Senhas com hash `scrypt` (padrão do Werkzeug); nunca em texto puro.
- CSRF habilitado globalmente (Flask-WTF); toda requisição POST exige token.
- Toda consulta de tarefa é restrita ao usuário logado — tentar abrir, editar,
  migrar ou excluir a tarefa de outro usuário devolve **404**.
- Backups de escopo `user` só podem ser baixados/restaurados pelo dono
  (admin pode tudo); backups `full` são exclusivos de admin.
- Nomes de snapshot são validados contra *path traversal*.
- Headers de segurança (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, CSP restritiva) e container rodando como usuário não-root
  com `no-new-privileges`.
- O app **não** tem HTTPS próprio: se for expor fora da LAN, coloque atrás de um
  proxy reverso (Caddy/Nginx/Traefik) e ative `SESSION_COOKIE_SECURE=true`.

---

## Solução de problemas

**Esqueci a senha do admin**

```bash
docker compose exec todonow flask --app wsgi set-password admin
```

**"O cadastro está desativado" / aba *Criar conta* cinza**
A política está em `auto` (e já existe usuário) ou em `no`. Para liberar o
autocadastro, use `ALLOW_REGISTRATION=yes` no `.env` e rode
`docker compose up -d`. Para criar contas manualmente, entre como admin em
**Usuários**.

**A porta está ocupada por outro serviço**
Veja quem está usando e escolha outra porta livre:

```bash
ss -ltnp | grep :8081          # quem está na porta (ou: sudo lsof -i :8081)
ss -ltn  | awk '{print $4}'    # todas as portas em escuta
```

Depois ajuste no `.env` e suba de novo:

```dotenv
TODONOW_PORT=9090              # qualquer porta livre no host
```

```bash
docker compose up -d           # recria o container com a porta nova
```

A porta interna acompanha `TODONOW_CONTAINER_PORT` (8081 por padrão); o `TODONOW_PORT`
muda só o lado do host. Se você
usa `TODONOW_BIND` (Tailscale), o endereço passa a ser `http://SEU-BIND:9090`.

**Calendário vazio**
O feed carrega do mês atual para frente; confira os filtros (situação/tipo) e se
as tarefas têm data dentro do período exibido.

**Quero inspecionar o banco**

```bash
docker compose exec todonow python -c "
import sqlite3;c=sqlite3.connect('/data/todonow.db')
print(c.execute('select count(*) from tasks').fetchone())"
```

**Apareceram tarefas repetidas no mesmo dia**
Sintoma das versões antigas, que copiavam a tarefa a cada migração. Faça um
backup e limpe:
```bash
docker compose exec todonow flask --app wsgi backup --scope full
docker compose exec todonow flask --app wsgi dedupe-tasks          # revise o plano
docker compose exec todonow flask --app wsgi dedupe-tasks --apply
# pares que sobraram em dias diferentes (artefatos da cópia antiga):
docker compose exec todonow flask --app wsgi dedupe-tasks --across-days --apply
```
Sem `--across-days` o comando mantém a tarefa mais antiga de cada grupo (mesmo
usuário, título, data e situação) e soma o histórico de migração no registro que
fica. Com `--across-days` ele também remove, **apenas quando há ligação de
migração ou uma entrada concluída atrás de outra aberta**, o registro antigo —
tarefas repetidas legítimas (um hábito diário, por exemplo) são preservadas.

**Erro de "E-mail inválido" com endereço da rede local**
Não acontece: domínios internos como `eu@casa.local`, `admin@homelab.local` ou
`ti@nas` são aceitos de propósito. O campo é opcional e apenas informativo — o
app nunca envia e-mails. Endereços realmente malformados (`invalido`,
`a@b`, com espaços) continuam sendo recusados.

**Restaurar um backup antigo após atualizar o app**
As migrações rodam sozinhas no start. Se algo der errado, restaure o snapshot
anterior em `/data/backups` pelo modo *mesclar*.

---

## Licença

MIT — use, modifique e hospede como quiser.
