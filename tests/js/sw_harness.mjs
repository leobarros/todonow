/* Harness do service worker (app/templates/sw.js), rodado pelo pytest via node.
 *
 * O sw.js não é um módulo: ele é um script que se registra em `self`. Aqui a
 * gente recria o mínimo do ambiente de worker (self, caches, fetch, Request,
 * Response) e dispara os eventos na mão, conferindo as decisões de cache que,
 * de outro jeito, só apareceriam num navegador de verdade.
 *
 * Uso: node sw_harness.mjs /caminho/para/sw.js
 * Sai com código 1 se qualquer verificação falhar.
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const ORIGEM = 'http://localhost';

/* ------------------------------------------------------------------ *
 * Dublês do ambiente do worker
 * ------------------------------------------------------------------ */

/** `new Request('/x')` funciona no navegador (resolve contra a base do
 *  worker) e falha no Node, que exige URL absoluta.
 *
 *  `navegar: true` marca a requisição como navegação: o undici recusa
 *  `mode: 'navigate'` (é coisa de navegador), então o valor é aplicado como
 *  propriedade própria, que é o que o sw.js lê. */
class RequestComBase extends Request {
  constructor(entrada, init) {
    const opcoes = { ...(init || {}) };
    const navegar = opcoes.navegar === true;
    delete opcoes.navegar;
    let alvo = entrada;
    if (typeof entrada === 'string') alvo = new URL(entrada, ORIGEM).href;
    super(alvo, opcoes);
    if (navegar) Object.defineProperty(this, 'mode', { value: 'navigate', configurable: true });
    this._urlOriginal = typeof entrada === 'string' ? entrada : entrada?.url;
  }
}

function chaveDe(requisicao) {
  if (typeof requisicao === 'string') return new URL(requisicao, ORIGEM).href;
  return requisicao.url;
}

class CacheFalso {
  constructor(nome) {
    this.nome = nome;
    this.itens = new Map();
  }
  async add(requisicao) {
    const resposta = await fetchFalso(requisicao);
    if (!resposta || !resposta.ok) throw new Error('falha ao guardar ' + chaveDe(requisicao));
    this.itens.set(chaveDe(requisicao), resposta);
  }
  async put(requisicao, resposta) {
    this.itens.set(chaveDe(requisicao), resposta);
  }
  async match(requisicao) {
    const guardado = this.itens.get(chaveDe(requisicao));
    return guardado ? guardado.clone() : undefined;
  }
  async keys() {
    return [...this.itens.keys()].map((url) => new RequestComBase(url));
  }
  async delete(requisicao) {
    return this.itens.delete(chaveDe(requisicao));
  }
}

class CacheStorageFalso {
  constructor() {
    this.caches = new Map();
  }
  async open(nome) {
    if (!this.caches.has(nome)) this.caches.set(nome, new CacheFalso(nome));
    return this.caches.get(nome);
  }
  async keys() {
    return [...this.caches.keys()];
  }
  async delete(nome) {
    return this.caches.delete(nome);
  }
  async match(requisicao) {
    for (const cache of this.caches.values()) {
      const achado = await cache.match(requisicao);
      if (achado) return achado;
    }
    return undefined;
  }
  nomes() {
    return [...this.caches.keys()];
  }
}

/* ------------------------------------------------------------------ *
 * Estado do teste
 * ------------------------------------------------------------------ */

const caches = new CacheStorageFalso();
const ouvintes = {};
const clientes = [];
let offline = false;
let falhasDeRede = 0;
const chamadas = [];
const log = [];

/** Páginas servidas pelo "servidor", por caminho. */
const paginas = new Map();
let respostaPadrao = () =>
  new Response('<html><body>padrão</body></html>', {
    status: 200,
    headers: { 'Content-Type': 'text/html; charset=utf-8' }
  });

async function fetchFalso(entrada, init) {
  const requisicao = typeof entrada === 'string' ? new RequestComBase(entrada, init) : entrada;
  const url = new URL(requisicao.url);
  chamadas.push({ metodo: requisicao.method, caminho: url.pathname, busca: url.search });

  if (offline) {
    falhasDeRede++;
    throw new TypeError('Failed to fetch (simulado)');
  }

  const caminho = url.pathname;
  if (paginas.has(caminho)) {
    const corpo = paginas.get(caminho);
    return new Response(corpo, { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  }
  if (caminho.startsWith('/static/')) {
    return new Response('/* asset ' + caminho + ' */', {
      status: 200,
      headers: { 'Content-Type': 'text/javascript' }
    });
  }
  if (caminho === '/offline') {
    return new Response('<html><body>TELA OFFLINE</body></html>', {
      status: 200,
      headers: { 'Content-Type': 'text/html; charset=utf-8' }
    });
  }
  if (caminho.startsWith('/calendar/feed')) {
    return new Response('[{"id":"1"}]', {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  }
  return respostaPadrao(requisicao);
}

const self = {
  location: new URL(ORIGEM + '/sw.js'),
  registration: {},
  addEventListener: (tipo, fn) => {
    (ouvintes[tipo] ||= []).push(fn);
  },
  skipWaiting: async () => {
    log.push('skipWaiting');
  },
  clients: {
    claim: async () => {
      log.push('claim');
    },
    matchAll: async () => clientes
  }
};

/* ------------------------------------------------------------------ *
 * Carga do sw.js
 * ------------------------------------------------------------------ */

const fonte = readFileSync(process.argv[2], 'utf8');
const sandbox = {
  self,
  caches,
  fetch: fetchFalso,
  Request: RequestComBase,
  Response,
  Headers,
  URL,
  AbortController,
  setTimeout,
  clearTimeout,
  console,
  Promise,
  JSON,
  Date,
  Math
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fonte, sandbox, { filename: 'sw.js' });

/* ------------------------------------------------------------------ *
 * Utilidades de disparo e verificação
 * ------------------------------------------------------------------ */

let falhas = 0;
let total = 0;

function conferir(descricao, condicao, detalhe) {
  total++;
  if (condicao) {
    console.log('  ok   ' + descricao);
  } else {
    falhas++;
    console.log('  FALHA ' + descricao + (detalhe ? ' → ' + detalhe : ''));
  }
}

async function disparar(tipo, extra = {}) {
  const evento = { ...extra, respondido: null, esperas: [] };
  evento.respondWith = (valor) => {
    evento.respondido = valor;
  };
  evento.waitUntil = (valor) => {
    evento.esperas.push(valor);
  };
  for (const fn of ouvintes[tipo] || []) fn(evento);
  await Promise.all(evento.esperas);
  if (evento.respondido) evento.resposta = await evento.respondido;
  return evento;
}

/** Deixa as promessas de segundo plano (revalidação) assentarem. */
const respirar = () => new Promise((resolve) => setTimeout(resolve, 10));

async function requisitar(caminho, extras = {}) {
  const requisicao = new RequestComBase(ORIGEM + caminho, extras);
  const evento = await disparar('fetch', { request: requisicao, ...extras.evento });
  return evento;
}

function paginaAutenticada(titulo) {
  return `<!doctype html><html data-todonow-auth="1" data-todonow-user="ana"><head><title>${titulo}</title></head><body>${titulo}</body></html>`;
}

function paginaDeLogin() {
  return '<!doctype html><html lang="pt-BR"><head><title>Entrar</title></head><body>login</body></html>';
}

function nomesDeCache() {
  return caches.nomes();
}

function cacheQueComeca(prefixo) {
  return nomesDeCache().find((nome) => nome.startsWith(prefixo));
}

/** Quantas páginas estão guardadas — sempre pelo nome atual do cache, porque
 *  apagar um cache invalida qualquer referência obtida antes. */
async function paginasGuardadas() {
  const nome = cacheQueComeca('todonow-pages-');
  if (!nome) return 0;
  return (await (await caches.open(nome)).keys()).length;
}

async function dadosGuardados() {
  const nome = cacheQueComeca('todonow-data-');
  if (!nome) return 0;
  return (await (await caches.open(nome)).keys()).length;
}

/* ------------------------------------------------------------------ *
 * Cenários
 * ------------------------------------------------------------------ */

console.log('sw.js — decisões de cache\n');

// 1. instalação precacheia o app inteiro
paginas.set('/', paginaAutenticada('Hoje'));
const instalacao = await disparar('install');
const shell = cacheQueComeca('todonow-shell-');
const shellCache = await caches.open(shell);
const precacheados = (await shellCache.keys()).map((r) => new URL(r.url).pathname);
conferir('install guarda os assets do app', precacheados.filter((p) => p.startsWith('/static/')).length >= 10,
  precacheados.length + ' entradas');
conferir('install guarda a tela offline', precacheados.includes('/offline'));
conferir('install assume o controle', log.includes('skipWaiting'));

// 2. ativação joga fora cache de versão antiga
await caches.open('todonow-shell-versao-velha');
const ativacao = await disparar('activate');
conferir('activate apaga cache antigo', !nomesDeCache().includes('todonow-shell-versao-velha'));
conferir('activate mantém o cache atual', nomesDeCache().includes(shell));
conferir('activate assume as abas abertas', log.includes('claim'));

// 3. navegação online: guarda a página autenticada
const primeiraVisita = await requisitar('/', { navegar: true });
conferir('navegação online responde da rede', primeiraVisita.resposta.status === 200);
conferir('página autenticada é guardada', (await paginasGuardadas()) === 1);

// 4. navegação offline: entrega a página guardada, marcada
offline = true;
const segundaVisita = await requisitar('/', { navegar: true });
const corpoOffline = await segundaVisita.resposta.text();
conferir('offline serve a página já vista', corpoOffline.includes('Hoje'));
conferir('página do cache vem marcada', corpoOffline.includes('todonow-served-from-cache'));
conferir('resposta do cache avisa pelo cabeçalho', segundaVisita.resposta.headers.get('X-Todonow-Offline') === '1');

// 5. navegação offline para tela nunca vista: cai na tela de "sem conexão"
const inedita = await requisitar('/monthly', { navegar: true });
const corpoInedito = await inedita.resposta.text();
conferir('tela nunca vista cai na página offline', corpoInedito.includes('TELA OFFLINE'));
offline = false;

// 6. estáticos: cache imediato e revalidação em segundo plano
const primeiroAsset = await requisitar('/static/css/todonow.css');
conferir('estático online vem da rede', (await primeiroAsset.resposta.text()).includes('todonow.css'));
await respirar();
offline = true;
const assetOffline = await requisitar('/static/css/todonow.css');
conferir('estático offline vem do cache', (await assetOffline.resposta.text()).includes('todonow.css'));
offline = false;

// 7. a página de login em si nunca é guardada (rota de sessão)
paginas.set('/auth/login', paginaDeLogin());
await requisitar('/auth/login', { navegar: true });
conferir('rota de login não é guardada', (await paginasGuardadas()) === 0);

// 8. logout/expiração: página sem a marca de autenticado em rota normal limpa
await requisitar('/', { navegar: true }); // página autenticada de novo
conferir('página autenticada guardada antes do teste', (await paginasGuardadas()) === 1);
paginas.set('/', paginaDeLogin());
await requisitar('/', { navegar: true });
conferir('sessão encerrada limpa as páginas guardadas', (await paginasGuardadas()) === 0);
paginas.set('/', paginaAutenticada('Hoje'));
await requisitar('/', { navegar: true }); // volta a guardar

// 9. comentário do calendário: último JSON bom serve de reserva
const feedOnline = await requisitar('/calendar/feed?start=2026-01-01&end=2026-02-01');
conferir('feed online responde normalmente', (await feedOnline.resposta.text()).includes('"id"'));
offline = true;
const feedOffline = await requisitar('/calendar/feed?start=2026-05-01&end=2026-06-01');
conferir('feed offline usa o último período visto', (await feedOffline.resposta.text()).includes('"id"'));
conferir('feed do cache vem marcado', feedOffline.resposta.headers.get('X-Todonow-Offline') === '1');

// 10. sem nada guardado, o feed falha em vez de mentir "nenhum evento"
const cacheDeDados = cacheQueComeca('todonow-data-');
if (cacheDeDados) await caches.delete(cacheDeDados);
offline = true;
const feedVazio = await requisitar('/calendar/feed');
conferir('feed sem cache devolve 503 (e não lista vazia)', feedVazio.resposta.status === 503);
offline = false;

// 11. rotas que dependem do servidor não passam pelo cache
for (const caminho of ['/sw.js', '/healthz']) {
  const evento = await requisitar(caminho);
  conferir(caminho + ' não é interceptado', evento.respondido === null);
}

// 12. escrita nunca é interceptada
const post = await disparar('fetch', {
  request: new RequestComBase(ORIGEM + '/tasks/quick', { method: 'POST' })
});
conferir('POST não é interceptado pelo worker', post.respondido === null);

// 13. navegação em rota de sessão offline cai na tela de "sem conexão"
offline = true;
const loginOffline = await requisitar('/auth/login', { navegar: true });
conferir('login offline mostra a tela de sem conexão', (await loginOffline.resposta.text()).includes('TELA OFFLINE'));
offline = false;

// 14. sincronização em segundo plano acorda as abas abertas
clientes.push({ postMessage: (msg) => log.push('msg:' + msg.type) });
await disparar('sync', { tag: 'todonow-outbox' });
conferir('sync pede para a página enviar a fila', log.includes('msg:todonow:flush'));

// 15. mensagem de limpeza (ao sair) apaga os dados do usuário
await requisitar('/calendar/feed'); // repõe o cache de dados
conferir('há dados guardados antes do purge', (await dadosGuardados()) > 0);
await disparar('message', { data: { type: 'todonow:purge' } });
conferir('purge limpa os dados do usuário', (await dadosGuardados()) === 0);

console.log('\n' + (total - falhas) + '/' + total + ' verificações passaram');
if (falhas) {
  console.log(falhas + ' falha(s)');
  process.exit(1);
}
