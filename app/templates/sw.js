{#- Service worker do todonow.

    Servido em /sw.js (veja app/pwa.py) para ter escopo na raiz do site. É um
    template Jinja para receber a lista de assets e a versão do cache, calculada
    a partir do conteúdo real de app/static: quando um CSS/JS muda, a versão
    muda, o navegador vê que este arquivo é outro e troca o cache antigo.

    Estratégias (o porquê de cada uma):
      * /static/**      → stale-while-revalidate: abre na hora do cache e
                          atualiza em segundo plano. Bom para rede instável.
      * páginas (HTML)  → rede primeiro, com prazo; cache depois. Uma rede que
                          "existe mas não responde" não pode travar a tela.
      * /calendar/feed  → rede primeiro, último JSON bom como reserva, para o
                          calendário continuar mostrando o que já foi visto.
      * POST/PUT/...    → nunca passam por aqui: quem cuida das escritas
                          offline é a fila em IndexedDB (js/offline.js).
-#}
'use strict';

const VERSION = {{ version|tojson }};
const APP_VERSION = {{ app_version|tojson }};
const SHELL = {{ shell_urls|tojson }};
const OFFLINE_URL = {{ offline_url|tojson }};
const NEVER_CACHE = {{ never_cache|tojson }};

const SHELL_CACHE = 'todonow-shell-' + VERSION;
const PAGE_CACHE = 'todonow-pages-' + VERSION;
const DATA_CACHE = 'todonow-data-' + VERSION;
const LIVE_CACHES = [SHELL_CACHE, PAGE_CACHE, DATA_CACHE];

/* Sem prazo, uma rede que aceita a conexão mas não responde deixaria a tela
   parada por dezenas de segundos. 4 s é o ponto em que já vale mostrar o que
   temos guardado. */
const NETWORK_TIMEOUT_MS = 4000;

/* Presente no <html> das páginas autenticadas. Página sem a marca significa
   usuário deslogado — e aí nenhuma página dele pode continuar no aparelho. */
const AUTH_MARKER = 'data-todonow-auth="1"';
const FROM_CACHE_MARKER = '<meta name="todonow-served-from-cache" content="1">';
const OFFLINE_HEADER = 'X-Todonow-Offline';

/* ------------------------------------------------------------------ *
 * Instalação: guarda o app inteiro (inclusive o calendário) para que
 * ele abra sem rede já na primeira vez em que a rede cair.
 * ------------------------------------------------------------------ */
self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      // Um asset que falhe não pode derrubar a instalação inteira.
      await Promise.all(
        SHELL.concat([OFFLINE_URL]).map(async (url) => {
          try {
            await cache.add(new Request(url, { cache: 'reload' }));
          } catch (erro) {
            /* segue: o que faltar cai na rede quando estiver online */
          }
        })
      );
      await self.skipWaiting();
    })()
  );
});

/* ------------------------------------------------------------------ *
 * Ativação: joga fora os caches de versões anteriores.
 * ------------------------------------------------------------------ */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const nomes = await caches.keys();
      await Promise.all(
        nomes.map((nome) => {
          if (nome.indexOf('todonow-') === 0 && LIVE_CACHES.indexOf(nome) === -1) {
            return caches.delete(nome);
          }
          return Promise.resolve(false);
        })
      );
      await self.clients.claim();
    })()
  );
});

/* ------------------------------------------------------------------ *
 * Roteamento
 * ------------------------------------------------------------------ */
self.addEventListener('fetch', (event) => {
  const request = event.request;
  // Escrita não se guarda: quem responde por elas é a fila offline.
  if (request.method !== 'GET') return;

  let url;
  try {
    url = new URL(request.url);
  } catch (erro) {
    return;
  }
  if (url.origin !== self.location.origin) return;

  if (precisaDoServidor(url.pathname)) {
    // Mesmo sem cache, uma navegação precisa de alguma tela de resposta.
    if (request.mode === 'navigate') event.respondWith(paginaSemCache(request));
    return;
  }

  if (url.pathname.indexOf('/static/') === 0) {
    event.respondWith(staleWhileRevalidate(request, SHELL_CACHE));
    return;
  }

  if (url.pathname.indexOf('/calendar/feed') === 0) {
    event.respondWith(estrategiaDoFeed(request));
    return;
  }

  if (pedeHtml(request)) {
    event.respondWith(estrategiaDePagina(request));
    return;
  }

  if (pedeJson(request)) {
    event.respondWith(redePrimeiro(request, DATA_CACHE));
    return;
  }

  event.respondWith(staleWhileRevalidate(request, DATA_CACHE));
});

/* ------------------------------------------------------------------ *
 * Estratégias
 * ------------------------------------------------------------------ */

/** Estáticos: responde do cache e atualiza em segundo plano. */
async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const guardado = await cache.match(request);

  const daRede = fetch(request)
    .then((resposta) => {
      if (resposta && resposta.ok) cache.put(request, resposta.clone());
      return resposta;
    })
    .catch(() => null);

  if (guardado) return guardado;

  const resposta = await daRede;
  if (resposta) return resposta;
  return new Response('', { status: 504, statusText: 'Sem conexão' });
}

/** Rede primeiro (com prazo), cache como reserva. */
async function redePrimeiro(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const resposta = await comPrazo(request, NETWORK_TIMEOUT_MS);
    if (resposta && resposta.ok) cache.put(request, resposta.clone());
    return resposta;
  } catch (erro) {
    const guardado = await cache.match(request);
    if (guardado) return marcarComoCache(guardado);
    return new Response('', { status: 503, statusText: 'Sem conexão' });
  }
}

/** Páginas: rede primeiro, última versão vista como reserva, offline por fim. */
async function estrategiaDePagina(request) {
  const cache = await caches.open(PAGE_CACHE);
  try {
    const resposta = await comPrazo(request, NETWORK_TIMEOUT_MS);
    if (resposta && resposta.ok && ehHtml(resposta)) {
      const corpo = await resposta.clone().text();
      if (corpo.indexOf(AUTH_MARKER) !== -1) {
        await cache.put(request, comCorpo(resposta, corpo));
      } else {
        // Chegou uma página de login: a sessão acabou. Nada do usuário fica.
        await limparCachesDoUsuario();
      }
    }
    return resposta;
  } catch (erro) {
    const guardado = await cache.match(request);
    if (guardado) {
      // Marcamos a página para o JS tirar avisos antigos (mensagens de "salvo
      // com sucesso" que já foram lidas) e avisar que o conteúdo é do cache.
      const headers = new Headers(guardado.headers);
      headers.set(OFFLINE_HEADER, '1');
      return new Response(inserirMarcadorDeCache(await guardado.text()), {
        status: guardado.status,
        statusText: guardado.statusText,
        headers
      });
    }
    if (request.mode === 'navigate') return paginaOffline();
    // Atualização parcial (região viva): melhor falhar do que devolver a
    // página offline inteira no lugar de um pedaço da tela.
    return new Response('', { status: 503, statusText: 'Sem conexão' });
  }
}

/** Navegação para rota que depende do servidor (login, backup). */
async function paginaSemCache(request) {
  try {
    const resposta = await fetch(request);
    if (resposta && resposta.ok && ehHtml(resposta) && ehRotaDeLogin(new URL(request.url).pathname)) {
      const corpo = await resposta.clone().text();
      // Página de login servida a quem estava logado = sessão terminou. Nada
      // do usuário pode continuar guardado no aparelho.
      if (corpo.indexOf(AUTH_MARKER) === -1) await limparCachesDoUsuario();
    }
    return resposta;
  } catch (erro) {
    return paginaOffline();
  }
}

/** Calendário: o último período carregado serve de reserva para qualquer data. */
async function estrategiaDoFeed(request) {
  const cache = await caches.open(DATA_CACHE);
  const chave = new Request('/calendar/feed');
  try {
    const resposta = await comPrazo(request, NETWORK_TIMEOUT_MS);
    if (resposta && resposta.ok) await cache.put(chave, resposta.clone());
    return resposta;
  } catch (erro) {
    const guardado = await cache.match(chave);
    if (guardado) return marcarComoCache(guardado);
    return new Response('[]', { status: 503, headers: { 'Content-Type': 'application/json' } });
  }
}

async function paginaOffline() {
  const cache = await caches.open(SHELL_CACHE);
  const guardada = await cache.match(OFFLINE_URL);
  if (guardada) return guardada;
  return new Response(
    '<!doctype html><html lang="pt-BR"><meta charset="utf-8">' +
      '<title>Sem conexão</title><h1>Sem conexão</h1>' +
      '<p>As alterações feitas offline continuam guardadas neste aparelho e ' +
      'serão enviadas quando a rede voltar.</p>',
    { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
  );
}

/* ------------------------------------------------------------------ *
 * Utilidades
 * ------------------------------------------------------------------ */

function precisaDoServidor(pathname) {
  return NEVER_CACHE.some((prefixo) => pathname.indexOf(prefixo) === 0);
}

function ehRotaDeLogin(pathname) {
  return pathname.indexOf('/auth/') === 0;
}

function pedeHtml(request) {
  if (request.mode === 'navigate') return true;
  return (request.headers.get('accept') || '').indexOf('text/html') !== -1;
}

function pedeJson(request) {
  return (request.headers.get('accept') || '').indexOf('application/json') !== -1;
}

function ehHtml(resposta) {
  return (resposta.headers.get('content-type') || '').indexOf('text/html') !== -1;
}

/** fetch com prazo: aborta e deixa o cache responder. */
function comPrazo(request, ms) {
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);
    fetch(request, { signal: controller.signal }).then(
      (resposta) => {
        clearTimeout(timer);
        resolve(resposta);
      },
      (erro) => {
        clearTimeout(timer);
        reject(erro);
      }
    );
  });
}

/** Cópia de uma resposta com o corpo já lido (o original segue para a página). */
function comCorpo(resposta, corpo) {
  return new Response(corpo, {
    status: resposta.status,
    statusText: resposta.statusText,
    headers: resposta.headers
  });
}

/** Marca a resposta como vinda do cache, para o JS avisar o usuário. */
function marcarComoCache(resposta) {
  const headers = new Headers(resposta.headers);
  headers.set(OFFLINE_HEADER, '1');
  return new Response(resposta.body, {
    status: resposta.status,
    statusText: resposta.statusText,
    headers
  });
}

function inserirMarcadorDeCache(html) {
  const corte = html.indexOf('</head>');
  if (corte === -1) return html;
  return html.slice(0, corte) + FROM_CACHE_MARKER + html.slice(corte);
}

async function limparCachesDoUsuario() {
  await Promise.all([caches.delete(PAGE_CACHE), caches.delete(DATA_CACHE)]);
}

/* ------------------------------------------------------------------ *
 * Sincronização em segundo plano e mensagens da página
 * ------------------------------------------------------------------ */

self.addEventListener('sync', (event) => {
  if (event.tag !== 'todonow-outbox') return;
  event.waitUntil(pedirParaSincronizar());
});

self.addEventListener('message', (event) => {
  const dados = event.data || {};
  if (dados.type === 'todonow:sync') {
    event.waitUntil(pedirParaSincronizar());
    return;
  }
  if (dados.type === 'todonow:purge') {
    event.waitUntil(limparCachesDoUsuario());
    return;
  }
  if (dados.type === 'todonow:version' && event.ports && event.ports[0]) {
    event.ports[0].postMessage({ version: VERSION, app: APP_VERSION });
  }
});

/* A fila vive no IndexedDB da página; aqui só acordamos quem está aberto.
   Se ninguém estiver aberto as ações continuam guardadas e saem na próxima
   vez que o app abrir. */
async function pedirParaSincronizar() {
  const clientes = await self.clients.matchAll({ includeUncontrolled: true, type: 'window' });
  clientes.forEach((cliente) => cliente.postMessage({ type: 'todonow:flush' }));
}
