/* todonow — modo offline.
 *
 * Três problemas diferentes, resolvidos juntos:
 *
 *   1. rede que cai
 *      O service worker (sw.js) guarda as telas visitadas e o app inteiro
 *      (Bootstrap, ícones, FullCalendar). As escritas feitas sem rede entram
 *      numa fila em IndexedDB e são reenviadas quando a conexão volta.
 *
 *   2. rede que "existe" mas não responde
 *      O sw.js espera no máximo 4 s antes de mostrar o que tem guardado. Aqui,
 *      o envio tem prazo e, se falhar, a ação vai para a fila em vez de virar
 *      uma tela de erro do navegador — com espera crescente entre tentativas.
 *
 *   3. usuário instável (fecha a aba, recarrega, clica duas vezes)
 *      * rascunho automático dos formulários longos;
 *      * situação da tarefa sempre como valor absoluto ("concluída"), nunca
 *        como inversão — dois cliques não desfazem o primeiro;
 *      * chave de idempotência por ação: se a resposta se perder no caminho, o
 *        reenvio é reconhecido pelo servidor e nada é duplicado.
 *
 * Sem JavaScript nada disso roda — e o app continua funcionando como sempre.
 */
(function () {
  'use strict';

  /* As decisões sem DOM (espera, anulação de ações repetidas, detecção de
     sessão expirada, rascunho) vivem em offline-core.js, onde podem ser
     conferidas direto no Node. Sem ele o app segue funcionando — só perde o
     modo offline. */
  var core = window.TodonowOfflineCore;
  if (!core) {
    if (window.console) window.console.warn('todonow: offline-core.js não carregou; modo offline desativado.');
    return;
  }

  var DB_NOME = 'todonow-offline';
  var DB_VERSAO = 1;
  var STORE = 'outbox';
  var INTERVALO_SINCRONIA = 45000;

  var tokenMeta = document.querySelector('meta[name="csrf-token"]');
  var usuarioMeta = document.documentElement.getAttribute('data-todonow-user') || '';

  /* ==================================================================
   * Utilidades
   * ================================================================== */

  function uuid() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      return window.crypto.randomUUID();
    }
    return 'op-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  }

  function estaOnline() {
    return navigator.onLine !== false;
  }

  function tokenCsrf() {
    if (tokenMeta && tokenMeta.content) return tokenMeta.content;
    return '';
  }

  function resolverUrl(alvo) {
    try {
      return new URL(alvo || window.location.pathname, window.location.href);
    } catch (erro) {
      return new URL(window.location.pathname, window.location.href);
    }
  }

  function lerJson(resposta) {
    return resposta
      .json()
      .then(function (dados) {
        return dados;
      })
      .catch(function () {
        return null;
      });
  }

  function idDaTarefa(elemento) {
    var linha = elemento && elemento.closest ? elemento.closest('[data-task-id]') : null;
    if (!linha) return null;
    var id = parseInt(linha.getAttribute('data-task-id'), 10);
    return isNaN(id) ? null : id;
  }

  function guardarSeguro(chave, valor) {
    try {
      window.localStorage.setItem(chave, valor);
      return true;
    } catch (erro) {
      return false; /* modo privado / cota cheia: seguimos sem rascunho */
    }
  }

  function lerSeguro(chave) {
    try {
      return window.localStorage.getItem(chave);
    } catch (erro) {
      return null;
    }
  }

  function apagarSeguro(chave) {
    try {
      window.localStorage.removeItem(chave);
    } catch (erro) {
      /* nada a fazer */
    }
  }

  /* ==================================================================
   * Fila em IndexedDB
   *
   * Se o navegador não tem IndexedDB (ou o usuário bloqueou), a fila vive só
   * na memória: perde-se ao fechar a aba, mas o app continua utilizável.
   * ================================================================== */

  var bancoPromessa = null;
  var memoria = [];

  function abrirBanco() {
    if (bancoPromessa) return bancoPromessa;
    bancoPromessa = new Promise(function (resolve, reject) {
      if (!window.indexedDB) {
        reject(new Error('IndexedDB indisponivel'));
        return;
      }
      var pedido = window.indexedDB.open(DB_NOME, DB_VERSAO);
      pedido.onupgradeneeded = function () {
        var banco = pedido.result;
        if (!banco.objectStoreNames.contains(STORE)) {
          var store = banco.createObjectStore(STORE, { keyPath: 'id' });
          store.createIndex('ts', 'ts');
        }
      };
      pedido.onsuccess = function () {
        resolve(pedido.result);
      };
      pedido.onerror = function () {
        reject(pedido.error || new Error('falha ao abrir IndexedDB'));
      };
      pedido.onblocked = function () {
        reject(new Error('IndexedDB bloqueado'));
      };
    });
    return bancoPromessa;
  }

  function comStore(modo, acao) {
    return abrirBanco().then(function (banco) {
      return new Promise(function (resolve, reject) {
        var transacao = banco.transaction(STORE, modo);
        var store = transacao.objectStore(STORE);
        var pedido;
        try {
          pedido = acao(store);
        } catch (erro) {
          reject(erro);
          return;
        }
        transacao.oncomplete = function () {
          resolve(pedido && 'result' in pedido ? pedido.result : undefined);
        };
        transacao.onerror = function () {
          reject(transacao.error);
        };
        transacao.onabort = function () {
          reject(transacao.error);
        };
      });
    });
  }

  function filaToda() {
    return comStore('readonly', function (store) {
      return store.getAll();
    })
      .then(function (itens) {
        return (itens || []).sort(function (a, b) {
          return a.ts - b.ts;
        });
      })
      .catch(function () {
        return memoria.slice().sort(function (a, b) {
          return a.ts - b.ts;
        });
      });
  }

  function gravar(op) {
    return comStore('readwrite', function (store) {
      return store.put(op);
    }).catch(function () {
      var indice = -1;
      for (var i = 0; i < memoria.length; i++) {
        if (memoria[i].id === op.id) indice = i;
      }
      if (indice === -1) memoria.push(op);
      else memoria[indice] = op;
    });
  }

  function remover(id) {
    return comStore('readwrite', function (store) {
      return store.delete(id);
    }).catch(function () {
      memoria = memoria.filter(function (op) {
        return op.id !== id;
      });
    });
  }

  /* ==================================================================
   * Enfileirar
   * ================================================================== */

  function camposDoFormulario(form) {
    var campos = {};
    var dados = new FormData(form);
    dados.forEach(function (valor, nome) {
      if (nome === 'csrf_token' || nome === 'idempotency_key') return;
      if (typeof valor === 'string') campos[nome] = valor;
    });
    return campos;
  }

  function formularioTemArquivo(form) {
    var arquivos = form.querySelectorAll('input[type="file"]');
    for (var i = 0; i < arquivos.length; i++) {
      if (arquivos[i].files && arquivos[i].files.length) return true;
    }
    return false;
  }

  function rotuloDoBotao(form, padrao) {
    var botao = form.querySelector('button[type="submit"], button:not([type])');
    if (!botao) return padrao;
    var texto = (botao.textContent || '').replace(/\s+/g, ' ').trim();
    return texto || padrao;
  }

  function enfileirar(dados) {
    var op = {
      id: uuid(),
      key: uuid(),
      ts: Date.now(),
      url: dados.url,
      method: dados.method || 'POST',
      fields: dados.fields || {},
      json: dados.json || null,
      label: dados.label || 'Alteração',
      kind: dados.kind || 'form',
      taskId: dados.taskId || null,
      user: dados.user || usuarioMeta,
      state: 'pending',
      attempts: 0,
      lastError: null,
      nextTryAt: 0
    };

    return filaToda()
      .then(function (fila) {
        var substituir = fila.filter(function (outra) {
          return outra.id !== op.id && outra.taskId && outra.taskId === op.taskId && core.deveSubstituir(outra, op);
        });
        return Promise.all(
          substituir.map(function (outra) {
            return remover(outra.id);
          })
        );
      })
      .then(function () {
        return gravar(op);
      })
      .then(function () {
        pedirSincronizacaoEmSegundoPlano();
        renderizarBarra();
        return op;
      });
  }

  function enfileirarFormulario(form, opcoes) {
    opcoes = opcoes || {};
    return enfileirar({
      url: opcoes.url || resolverUrl(form.getAttribute('action')).pathname + (opcoes.query || ''),
      method: 'POST',
      fields: opcoes.campos || camposDoFormulario(form),
      json: opcoes.json || null,
      label: opcoes.label || rotuloDoBotao(form, 'Alteração pendente'),
      kind: opcoes.kind || 'form',
      taskId: opcoes.taskId !== undefined ? opcoes.taskId : idDaTarefa(form)
    }).then(function (op) {
      /* A ação já está gravada; o enfeite da tela (selo na linha, rascunho)
         não pode rejeitar a promessa — quem chamou entenderia como falha e
         tentaria guardar de novo, criando duas ações. */
      try {
        marcarPendente(form, op);
        limparRascunho(form);
      } catch (erro) {
        if (window.console) window.console.warn('todonow: falha ao marcar a pendência na tela', erro);
      }
      return op;
    });
  }

  /* ==================================================================
   * Envio
   * ================================================================== */

  function cabecalhos(op) {
    var headers = { 'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json' };
    var token = tokenCsrf() || (op && op.fields ? op.fields.csrf_token : '');
    if (token) headers['X-CSRFToken'] = token;
    if (op && op.key) headers['X-Idempotency-Key'] = op.key;
    return headers;
  }

  function corpoDaOp(op) {
    if (op.json) return JSON.stringify(op.json);
    var dados = new FormData();
    var campos = op.fields || {};
    Object.keys(campos).forEach(function (nome) {
      dados.append(nome, campos[nome]);
    });
    var token = tokenCsrf();
    if (token) dados.set('csrf_token', token);
    return dados;
  }

  function tentarEnviar(op) {
    op.attempts = (op.attempts || 0) + 1;
    var corpo = corpoDaOp(op);
    var headers = cabecalhos(op);
    if (op.json) headers['Content-Type'] = 'application/json';

    return fetch(op.url, {
      method: op.method || 'POST',
      headers: headers,
      body: corpo,
      credentials: 'same-origin'
    })
      .then(function (resposta) {
        if (resposta.ok || resposta.status === 409) {
          return remover(op.id).then(function () {
            return 'ok';
          });
        }
        if (resposta.status === 404 && op.kind === 'delete') {
          /* Já não existe: o objetivo era esse mesmo. */
          return remover(op.id).then(function () {
            return 'ok';
          });
        }
        if (resposta.status >= 500 || resposta.status === 429 || resposta.status === 408) {
          op.lastError = 'O servidor não respondeu como esperado.';
          op.nextTryAt = Date.now() + core.esperaDe(op.attempts);
          return gravar(op).then(function () {
            return 'servidor';
          });
        }
        return lerJson(resposta).then(function (dados) {
          if (core.pareceProblemaDeSessao(resposta, dados)) {
            op.state = 'blocked';
            op.lastError = 'Sua sessão expirou. Entre novamente para enviar o que ficou guardado.';
            return gravar(op).then(function () {
              return 'sessao';
            });
          }
          op.state = 'failed';
          op.lastError = core.mensagemDeErro(dados) || 'O servidor recusou esta alteração.';
          return gravar(op).then(function () {
            return 'conteudo';
          });
        });
      })
      .catch(function () {
        op.lastError = 'Sem conexão.';
        op.nextTryAt = Date.now() + core.esperaDe(op.attempts);
        return gravar(op).then(function () {
          return 'rede';
        });
      });
  }

  var sincronizando = false;
  var sincronizacaoAtual = null;

  function sincronizar() {
    if (sincronizando) return sincronizacaoAtual;
    sincronizando = true;
    sincronizacaoAtual = filaToda()
      .then(function (fila) {
        var pendentes = fila.filter(function (op) {
          return op.state === 'pending';
        });
        if (!pendentes.length || !estaOnline()) {
          return { enviadas: 0, restantes: pendentes.length };
        }
        definirSincronizando(true);
        var resumo = { enviadas: 0, restantes: pendentes.length };
        var cadeia = Promise.resolve();

        pendentes.forEach(function (op) {
          cadeia = cadeia.then(function () {
            if (op.nextTryAt && op.nextTryAt > Date.now()) return null;
            if (op.user && usuarioMeta && op.user !== usuarioMeta) {
              op.state = 'other';
              op.lastError = 'Esta alteração é da conta ' + op.user + '.';
              return gravar(op);
            }
            return tentarEnviar(op).then(function (resultado) {
              if (resultado === 'ok') {
                resumo.enviadas++;
                resumo.restantes--;
                return null;
              }
              /* Qualquer outro resultado interrompe a fila: manter a ordem é
                 mais importante que insistir — a próxima ação pode depender
                 desta (criar e depois concluir, por exemplo). */
              return Promise.reject(new Error(resultado));
            });
          });
        });

        return cadeia.catch(function () {
          return null;
        }).then(function () {
          return resumo;
        });
      })
      .then(function (resumo) {
        sincronizando = false;
        definirSincronizando(false);
        if (resumo.enviadas) {
          limparMarcasDePendencia();
          atualizarTela();
          avisarSucesso(resumo.enviadas);
        }
        renderizarBarra();
        return resumo;
      })
      .catch(function () {
        sincronizando = false;
        definirSincronizando(false);
        renderizarBarra();
        return { enviadas: 0, restantes: 0 };
      });
    return sincronizacaoAtual;
  }

  /* ==================================================================
   * API usada pelo resto do app (todonow.js, calendar.js, notes.js)
   * ================================================================== */

  /**
   * Envia um formulário; se a rede falhar (ou não houver), guarda na fila.
   * Devolve { estado: 'enviado' | 'na-fila' | 'invalido', dados, op }.
   */
  function enviarOuEnfileirar(form, opcoes) {
    opcoes = opcoes || {};
    var url = opcoes.url || resolverUrl(form.getAttribute('action')).pathname + (opcoes.query || '');

    if (!estaOnline()) {
      return enfileirarFormulario(form, opcoes).then(function (op) {
        return { estado: 'na-fila', op: op, dados: null };
      });
    }

    var headers = cabecalhos(null);
    var corpo;
    if (opcoes.json) {
      headers['Content-Type'] = 'application/json';
      corpo = JSON.stringify(opcoes.json);
    } else if (opcoes.campos) {
      corpo = new FormData();
      Object.keys(opcoes.campos).forEach(function (nome) {
        corpo.append(nome, opcoes.campos[nome]);
      });
      var token = tokenCsrf();
      if (token) corpo.set('csrf_token', token);
    } else {
      corpo = new FormData(form);
    }

    return fetch(url, { method: 'POST', headers: headers, body: corpo, credentials: 'same-origin' })
      .then(function (resposta) {
        return lerJson(resposta).then(function (dados) {
          if (resposta.ok && (!dados || dados.ok !== false)) {
            limparRascunho(form);
            return { estado: 'enviado', dados: dados, resposta: resposta };
          }
          if (resposta.status >= 500 || resposta.status === 429) {
            return enfileirarFormulario(form, opcoes).then(function (op) {
              return { estado: 'na-fila', op: op, dados: null };
            });
          }
          return { estado: 'invalido', dados: dados, resposta: resposta };
        });
      })
      .catch(function () {
        return enfileirarFormulario(form, opcoes).then(function (op) {
          return { estado: 'na-fila', op: op, dados: null };
        });
      });
  }

  /**
   * Mesma ideia de :func:`enviarOuEnfileirar`, mas para pedidos JSON que não
   * vêm de um formulário — é o caso do calendário, que envia {id, date} ao
   * arrastar um cartão.
   */
  function enviarOuEnfileirarJson(url, corpo, opcoes) {
    opcoes = opcoes || {};
    var descricao = {
      url: url,
      method: 'POST',
      json: corpo,
      label: opcoes.label || 'Alteração',
      kind: opcoes.kind || 'json',
      taskId: opcoes.taskId || null
    };

    function guardarNaFila() {
      return enfileirar(descricao).then(function (op) {
        return { estado: 'na-fila', op: op, dados: null };
      });
    }

    if (!estaOnline()) return guardarNaFila();

    var headers = cabecalhos(null);
    headers['Content-Type'] = 'application/json';

    return fetch(url, { method: 'POST', headers: headers, body: JSON.stringify(corpo), credentials: 'same-origin' })
      .then(function (resposta) {
        return lerJson(resposta).then(function (dados) {
          if (resposta.ok && (!dados || dados.ok !== false)) {
            return { estado: 'enviado', dados: dados, resposta: resposta };
          }
          if (resposta.status >= 500 || resposta.status === 429) return guardarNaFila();
          return { estado: 'invalido', dados: dados, resposta: resposta };
        });
      })
      .catch(guardarNaFila);
  }

  /** Devolve à fila o que falhou, para uma nova tentativa a pedido do usuário. */
  function retentarTudo() {
    return filaToda()
      .then(function (fila) {
        var travadas = fila.filter(function (op) {
          return op.state === 'failed' || op.state === 'blocked' || op.state === 'other';
        });
        return Promise.all(
          travadas.map(function (op) {
            op.state = 'pending';
            op.attempts = 0;
            op.nextTryAt = 0;
            return gravar(op);
          })
        );
      })
      .then(function () {
        return sincronizar();
      });
  }

  /** Marca a linha da tarefa como "ainda não confirmada pelo servidor". */
  function marcarPendente(origem, op) {
    var linha = origem && origem.closest ? origem.closest('[data-task-id]') : null;
    if (!linha) return;
    linha.classList.add('is-pending-sync');
    if (op && op.kind === 'delete') linha.classList.add('is-pending-remove');
    if (linha.querySelector('.pending-flag')) return;
    var marca = document.createElement('span');
    marca.className = 'pending-flag';
    marca.innerHTML = '<i class="bi bi-cloud-arrow-up"></i>';
    marca.title = 'Guardado neste aparelho — será enviado quando a rede voltar.';
    var alvo = linha.querySelector('.task-meta') || linha;
    alvo.appendChild(marca);
  }

  function limparMarcasDePendencia() {
    Array.prototype.forEach.call(document.querySelectorAll('.pending-flag'), function (marca) {
      marca.remove();
    });
    Array.prototype.forEach.call(document.querySelectorAll('.is-pending-sync, .is-pending-remove'), function (linha) {
      linha.classList.remove('is-pending-sync', 'is-pending-remove');
    });
  }

  function atualizarTela() {
    if (window.Todonow && typeof window.Todonow.refreshLiveRegions === 'function') {
      window.Todonow.refreshLiveRegions();
    }
    document.dispatchEvent(new CustomEvent('todonow:refresh'));
  }

  function pedirSincronizacaoEmSegundoPlano() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.ready
      .then(function (registro) {
        if (registro.sync && typeof registro.sync.register === 'function') {
          return registro.sync.register('todonow-outbox');
        }
        return null;
      })
      .catch(function () {
        /* navegador sem Background Sync: a fila sai na próxima abertura */
      });
  }

  /* ==================================================================
   * Rascunhos: o que o usuário digitou não se perde
   *
   * Só nos formulários marcados com data-draft="auto" — de propósito: um
   * rascunho que volta na hora errada atrapalha mais do que ajuda.
   * ================================================================== */

  var RASCUNHO_PREFIXO = 'todonow-draft:';
  var RASCUNHO_VALIDADE = 7 * 24 * 60 * 60 * 1000;
  var RASCUNHO_LIMITE = 100 * 1024;

  function formulariosComRascunho() {
    return Array.prototype.filter.call(document.querySelectorAll('form[data-draft="auto"]'), function (form) {
      return !!form.querySelector('textarea, input:not([type="hidden"])');
    });
  }

  function chaveDoRascunho(form) {
    var nomes = Array.prototype.map
      .call(form.elements, function (elemento) {
        return elemento.name || '';
      })
      .filter(Boolean)
      .sort()
      .join(',');
    var identidade = form.getAttribute('data-draft-key') || form.getAttribute('action') || '';
    return RASCUNHO_PREFIXO + window.location.pathname + '#' + identidade + '#' + core.assinatura(nomes);
  }

  function valoresDoFormulario(form) {
    /* O editor de anotações converte o modo visual para Markdown aqui. */
    form.dispatchEvent(new CustomEvent('todonow:collect', { bubbles: true }));

    var valores = {};
    Array.prototype.forEach.call(form.elements, function (elemento) {
      var nome = elemento.name;
      if (!nome || elemento.disabled) return;
      if (nome === 'csrf_token' || nome === 'idempotency_key') return;
      var tipo = (elemento.type || '').toLowerCase();
      if (tipo === 'password' || tipo === 'file' || tipo === 'submit' || tipo === 'button' || tipo === 'hidden') return;
      if (tipo === 'checkbox' || tipo === 'radio') {
        if (!elemento.checked) return;
        if (valores[nome] === undefined) valores[nome] = elemento.value;
        else if (Object.prototype.toString.call(valores[nome]) === '[object Array]') valores[nome].push(elemento.value);
        else valores[nome] = [valores[nome], elemento.value];
        return;
      }
      if (elemento.tagName === 'SELECT' && elemento.multiple) {
        valores[nome] = Array.prototype.map.call(elemento.selectedOptions, function (opcao) {
          return opcao.value;
        });
        return;
      }
      valores[nome] = elemento.value;
    });
    return valores;
  }

  function aplicarValores(form, valores) {
    Array.prototype.forEach.call(form.elements, function (elemento) {
      var nome = elemento.name;
      if (!nome || !(nome in valores)) return;
      var valor = valores[nome];
      var tipo = (elemento.type || '').toLowerCase();
      if (tipo === 'checkbox' || tipo === 'radio') {
        var lista = Object.prototype.toString.call(valor) === '[object Array]' ? valor : [valor];
        elemento.checked = lista.indexOf(elemento.value) !== -1;
      } else if (elemento.tagName === 'SELECT' && elemento.multiple) {
        var escolhidos = Object.prototype.toString.call(valor) === '[object Array]' ? valor : [valor];
        Array.prototype.forEach.call(elemento.options, function (opcao) {
          opcao.selected = escolhidos.indexOf(opcao.value) !== -1;
        });
      } else {
        elemento.value = valor;
      }
      elemento.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }

  function lerRascunho(form) {
    var bruto = lerSeguro(chaveDoRascunho(form));
    if (!bruto) return null;
    var dados;
    try {
      dados = JSON.parse(bruto);
    } catch (erro) {
      apagarSeguro(chaveDoRascunho(form));
      return null;
    }
    if (!dados || typeof dados !== 'object') return null;
    if (!dados.savedAt || Date.now() - dados.savedAt > RASCUNHO_VALIDADE) {
      apagarSeguro(chaveDoRascunho(form));
      return null;
    }
    return dados;
  }

  function salvarRascunho(form, extra) {
    var valores = valoresDoFormulario(form);
    var vazio = Object.keys(valores).every(function (nome) {
      var valor = valores[nome];
      if (Object.prototype.toString.call(valor) === '[object Array]') return valor.length === 0;
      return !valor;
    });
    if (vazio) {
      apagarSeguro(chaveDoRascunho(form));
      return;
    }
    var dados = {
      values: valores,
      baseline: form.__todonowBaseline || valores,
      savedAt: Date.now(),
      submittedAt: (extra && extra.submittedAt) || null
    };
    var texto = JSON.stringify(dados);
    if (texto.length > RASCUNHO_LIMITE) return; /* grande demais para valer a pena */
    guardarSeguro(chaveDoRascunho(form), texto);
  }

  function limparRascunho(form) {
    if (!form || !form.getAttribute || form.getAttribute('data-draft') !== 'auto') return;
    apagarSeguro(chaveDoRascunho(form));
  }

  function avisoDeRascunho(form, dados, automatico) {
    var caixa = document.createElement('div');
    caixa.className = 'alert alert-info d-flex flex-wrap align-items-center gap-2 rascunho-aviso';
    caixa.setAttribute('role', 'status');

    var texto = document.createElement('span');
    texto.className = 'flex-grow-1';
    texto.innerHTML = automatico
      ? '<i class="bi bi-life-preserver me-1"></i>Recuperamos o que você tinha digitado nesta tela.'
      : '<i class="bi bi-life-preserver me-1"></i>Existe um rascunho desta tela que não chegou a ser salvo.';

    var restaurar = document.createElement('button');
    restaurar.type = 'button';
    restaurar.className = 'btn btn-sm btn-primary';
    restaurar.textContent = 'Restaurar';
    restaurar.addEventListener('click', function () {
      aplicarValores(form, dados.values);
      caixa.remove();
    });

    var descartar = document.createElement('button');
    descartar.type = 'button';
    descartar.className = 'btn btn-sm btn-outline-secondary';
    descartar.textContent = 'Descartar';
    descartar.addEventListener('click', function () {
      apagarSeguro(chaveDoRascunho(form));
      caixa.remove();
    });

    caixa.appendChild(texto);
    caixa.appendChild(restaurar);
    caixa.appendChild(descartar);
    form.parentNode.insertBefore(caixa, form);
  }

  function prepararRascunhos() {
    formulariosComRascunho().forEach(function (form) {
      var atual = valoresDoFormulario(form);
      form.__todonowBaseline = atual;
      var dados = lerRascunho(form);
      if (!dados) return;

      var decisao = core.decisaoDeRascunho(dados, atual);
      if (decisao === 'perguntar') {
        /* O formulário foi enviado, mas nunca vimos a tela de confirmação:
           ou deu certo, ou o envio se perdeu. Quem sabe é o usuário. */
        avisoDeRascunho(form, dados, false);
        return;
      }
      if (decisao === 'descartar') {
        /* O que o servidor mandou mudou desde o rascunho: o dado atual manda. */
        apagarSeguro(chaveDoRascunho(form));
        return;
      }
      if (decisao === 'restaurar') {
        aplicarValores(form, dados.values);
        avisoDeRascunho(form, dados, true);
      }
    });
  }

  function agendarRascunho(form) {
    if (form.__todonowTimer) window.clearTimeout(form.__todonowTimer);
    form.__todonowTimer = window.setTimeout(function () {
      salvarRascunho(form, null);
    }, 500);
  }

  /** O alvo pode ser um controle (tem .form) ou o editor visual (não tem). */
  function formularioDoAlvo(alvo) {
    if (!alvo) return null;
    if (alvo.form) return alvo.form;
    return alvo.closest ? alvo.closest('form') : null;
  }

  /* ==================================================================
   * Interceptação de formulários
   * ================================================================== */

  function podeEnfileirar(form) {
    if (!form || form.tagName !== 'FORM') return false;
    var url = resolverUrl(form.getAttribute('action'));
    return core.deveEnfileirar({
      marcado: form.getAttribute('data-offline'),
      metodo: form.getAttribute('method') || 'get',
      caminho: url.pathname,
      mesmaOrigem: url.origin === window.location.origin,
      /* Arquivo não cabe na fila: melhor a tela de erro do navegador que uma
         ação que nunca vai sair. */
      temArquivo: formularioTemArquivo(form)
    });
  }

  function ehSaida(form) {
    var url = resolverUrl(form.getAttribute('action'));
    return url.pathname === '/auth/logout';
  }

  /**
   * Depois de guardar um formulário na fila, sai de cena como o servidor faria.
   *
   * Sem isso o formulário continua preenchido e com o botão ativo — um segundo
   * clique criaria *outra* tarefa (é uma criação nova, com chave nova), e o
   * usuário veria duplicidade. O campo `next` é o mesmo que o servidor usaria
   * para redirecionar.
   */
  function sairDoFormularioGuardado(form) {
    var destino = form.querySelector('input[name="next"]');
    var bruto = destino && destino.value ? destino.value : '';
    var atual = window.location.pathname + window.location.search;

    if (bruto) {
      var url = resolverUrl(bruto);
      // Só destino do próprio app: o campo vem do formulário e não pode virar
      // um redirecionamento para fora.
      if (url.origin === window.location.origin && url.pathname + url.search !== atual) {
        window.setTimeout(function () {
          window.location.assign(url.pathname + url.search);
        }, 700);
        return;
      }
    }

    var botao = form.querySelector('button[type="submit"]');
    if (botao) {
      botao.disabled = true;
      botao.setAttribute('title', 'Guardado neste aparelho — será enviado quando a rede voltar.');
    }
  }

  /* Preenchido por renderizarBarra(); o handler de submit precisa decidir na
     hora, sem esperar o IndexedDB (que é assíncrono). */
  var resumoFila = { pendentes: 0, problemas: 0 };

  document.addEventListener(
    'submit',
    function (evento) {
      var form = evento.target;
      if (!form || form.tagName !== 'FORM') return;

      /* Sair com alterações guardadas merece uma pausa. Aqui só marcamos o
         formulário: quem mostra o modal é o todonow.js, no mesmo evento. */
      if (ehSaida(form) && form.dataset.confirmed !== '1' && resumoFila.pendentes > 0) {
        form.dataset.confirm =
          'Existem ' + resumoFila.pendentes + ' alteração(ões) ainda não enviadas neste aparelho.';
        form.dataset.confirmLabel = 'Sair mesmo assim';
        form.dataset.confirmNote =
          'Elas continuam guardadas e serão enviadas no próximo acesso desta conta.';
        return;
      }

      if (!podeEnfileirar(form)) return;

      /* Com confirmação pendente, quem decide primeiro é o modal. */
      if ((form.dataset.confirm || form.dataset.confirmLabel) && form.dataset.confirmed !== '1') return;

      if (estaOnline()) {
        /* Online seguimos com o envio normal do navegador — mas guardamos o
           rascunho para o caso de a resposta nunca chegar. */
        if (form.getAttribute('data-draft') === 'auto') salvarRascunho(form, { submittedAt: Date.now() });
        return;
      }

      evento.preventDefault();
      var opcoes = {
        label: form.getAttribute('data-offline-label') || rotuloDoBotao(form, 'Alteração guardada')
      };
      enfileirarFormulario(form, opcoes).then(function () {
        avisar('Sem conexão: guardamos esta alteração e enviamos quando a rede voltar.', 'warning');
        sairDoFormularioGuardado(form);
      });
    },
    true
  );

  document.addEventListener(
    'input',
    function (evento) {
      var form = formularioDoAlvo(evento.target);
      if (form && form.getAttribute('data-draft') === 'auto') agendarRascunho(form);
    },
    true
  );

  document.addEventListener(
    'change',
    function (evento) {
      var form = formularioDoAlvo(evento.target);
      if (form && form.getAttribute('data-draft') === 'auto') agendarRascunho(form);
    },
    true
  );

  document.addEventListener(
    'submit',
    function (evento) {
      var form = evento.target;
      if (!form || form.tagName !== 'FORM') return;
      /* O modal de confirmação reenvia o mesmo formulário; essa passagem não
         conta como "clique repetido". */
      if (form.dataset.confirmed === '1') return;
      if (!podeEnfileirar(form)) return;

      var agora = Date.now();
      var anterior = parseInt(form.dataset.enviandoUltimo || '0', 10);
      if (anterior && agora - anterior < 800) {
        /* Segundo clique no mesmo segundo: quase sempre é engano. A chave de
           idempotência também protegeria, mas nem vale sair o pedido. */
        evento.preventDefault();
        return;
      }
      form.dataset.enviandoUltimo = String(agora);

      var botao = form.querySelector('button[type="submit"]');
      if (!botao || botao.disabled) return;
      botao.disabled = true;
      window.setTimeout(function () {
        botao.disabled = false;
      }, 1000);
    },
    false
  );

  /* ==================================================================
   * Barra de estado
   * ================================================================== */

  var barra = null;
  var barraTexto = null;
  var barraContagem = null;
  var barraLista = null;
  var barraBotao = null;
  var sucessoAte = 0;
  var enviando = 0;

  function garantirBarra() {
    if (barra) return barra;
    barra = document.createElement('div');
    barra.id = 'offline-bar';
    barra.className = 'offline-bar d-none';
    barra.setAttribute('role', 'region');
    barra.setAttribute('aria-label', 'Estado da conexão e das alterações guardadas');
    barra.innerHTML =
      '<div class="offline-bar-linha">' +
      '<span class="offline-icone" aria-hidden="true"></span>' +
      '<span class="offline-texto" role="status" aria-live="polite"></span>' +
      '<button type="button" class="btn btn-sm btn-primary offline-enviar d-none">Enviar agora</button>' +
      '<button type="button" class="btn btn-sm btn-outline-secondary offline-ver d-none" aria-expanded="false"></button>' +
      '</div>' +
      '<ul class="offline-lista" hidden></ul>';
    document.body.appendChild(barra);

    barraTexto = barra.querySelector('.offline-texto');
    barraLista = barra.querySelector('.offline-lista');
    barraContagem = barra.querySelector('.offline-ver');
    barraBotao = barra.querySelector('.offline-enviar');

    barraBotao.addEventListener('click', function () {
      retentarTudo();
    });
    barraContagem.addEventListener('click', function () {
      var escondida = barraLista.hasAttribute('hidden');
      if (escondida) barraLista.removeAttribute('hidden');
      else barraLista.setAttribute('hidden', '');
      barraContagem.setAttribute('aria-expanded', escondida ? 'true' : 'false');
    });
    return barra;
  }

  function definirSincronizando(ativo) {
    enviando += ativo ? 1 : -1;
    if (enviando < 0) enviando = 0;
  }

  function descreverOp(op) {
    var quando = new Date(op.ts);
    var hora = quando.getHours().toString().padStart(2, '0') + ':' + quando.getMinutes().toString().padStart(2, '0');
    return { texto: op.label, hora: hora, erro: op.lastError };
  }

  function renderizarBarra() {
    var elemento = garantirBarra();
    var paginaDoCache = !!document.querySelector('meta[name="todonow-served-from-cache"]');

    filaToda().then(function (fila) {
      var pendentes = fila.filter(function (op) {
        return op.state !== 'ok';
      });
      var problemas = pendentes.filter(function (op) {
        return op.state === 'failed' || op.state === 'blocked' || op.state === 'other';
      });

      resumoFila.pendentes = pendentes.length;
      resumoFila.problemas = problemas.length;

      if (!pendentes.length && !problemas.length && estaOnline() && !paginaDoCache && Date.now() > sucessoAte) {
        elemento.classList.add('d-none');
        document.body.classList.remove('has-offline-bar');
        return;
      }

      elemento.classList.remove('d-none');
      document.body.classList.add('has-offline-bar');
      elemento.classList.remove('is-offline', 'is-problema', 'is-ok');
      barraBotao.classList.add('d-none');
      barraContagem.classList.add('d-none');
      barraLista.setAttribute('hidden', '');

      if (problemas.length) {
        elemento.classList.add('is-problema');
        barraTexto.textContent =
          problemas.length === 1
            ? problemas[0].lastError || 'Uma alteração não pôde ser enviada.'
            : problemas.length + ' alterações não puderam ser enviadas.';
        barraBotao.classList.remove('d-none');
        barraBotao.textContent = 'Tentar de novo';
      } else if (enviando) {
        elemento.classList.add('is-offline');
        barraTexto.textContent = 'Enviando ' + pendentes.length + ' alteração(ões) guardada(s)…';
      } else if (pendentes.length) {
        elemento.classList.add('is-offline');
        barraTexto.textContent = estaOnline()
          ? pendentes.length + ' alteração(ões) guardada(s) neste aparelho aguardando envio.'
          : 'Sem conexão — ' + pendentes.length + ' alteração(ões) guardada(s) neste aparelho.';
        barraBotao.classList.remove('d-none');
        barraBotao.textContent = 'Enviar agora';
      } else if (Date.now() <= sucessoAte) {
        elemento.classList.add('is-ok');
        barraTexto.textContent = 'Tudo sincronizado.';
      } else if (paginaDoCache) {
        elemento.classList.add('is-offline');
        barraTexto.textContent = 'Sem conexão — mostrando a última versão salva desta tela.';
      }

      if (pendentes.length) {
        barraContagem.classList.remove('d-none');
        barraContagem.textContent = 'Ver ' + pendentes.length;
        barraLista.innerHTML = '';
        pendentes.forEach(function (op) {
          var info = descreverOp(op);
          var item = document.createElement('li');
          item.className = 'offline-item' + (op.state === 'pending' ? '' : ' is-problema');

          var texto = document.createElement('span');
          texto.className = 'offline-item-texto';
          texto.textContent = info.texto;

          var hora = document.createElement('span');
          hora.className = 'offline-item-hora';
          hora.textContent = info.hora;

          var descartar = document.createElement('button');
          descartar.type = 'button';
          descartar.className = 'btn btn-sm btn-link text-danger p-0';
          descartar.textContent = 'descartar';
          descartar.addEventListener('click', function () {
            remover(op.id).then(function () {
              limparMarcasDePendencia();
              renderizarBarra();
            });
          });

          item.appendChild(texto);
          if (info.erro) {
            var erro = document.createElement('span');
            erro.className = 'offline-item-erro';
            erro.textContent = info.erro;
            item.appendChild(erro);
          }
          item.appendChild(hora);
          item.appendChild(descartar);
          barraLista.appendChild(item);
        });
      }
    });
  }

  function avisar(mensagem, tom) {
    var elemento = garantirBarra();
    elemento.classList.remove('d-none');
    barraTexto.textContent = mensagem;
    elemento.classList.add(tom === 'danger' ? 'is-problema' : 'is-offline');
  }

  function avisarSucesso(quantidade) {
    sucessoAte = Date.now() + 4000;
    var elemento = garantirBarra();
    elemento.classList.remove('d-none');
    elemento.classList.add('is-ok');
    barraTexto.textContent = quantidade === 1 ? 'Alteração enviada.' : quantidade + ' alterações enviadas.';
    window.setTimeout(function () {
      renderizarBarra();
    }, 4200);
  }

  /* ==================================================================
   * Service worker e ciclo de vida
   * ================================================================== */

  function registrarServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker
      .register('/sw.js', { scope: '/' })
      .then(function (registro) {
        if (registro.sync && typeof registro.sync.register === 'function') {
          registro.sync.register('todonow-outbox').catch(function () {});
        }
      })
      .catch(function () {
        /* Sem service worker o app funciona; só não abre sem rede. */
      });

    navigator.serviceWorker.addEventListener('message', function (evento) {
      if (evento.data && evento.data.type === 'todonow:flush') sincronizar();
    });
  }

  function aoVoltarARede() {
    renderizarBarra();
    sincronizar();
  }

  function iniciar() {
    /* Página servida do cache: os avisos ("tarefa criada") que vieram junto são
       de uma visita anterior e já foram lidos. Repeti-los confunde. */
    if (document.querySelector('meta[name="todonow-served-from-cache"]')) {
      var pilhaDeAvisos = document.querySelector('.flash-stack');
      if (pilhaDeAvisos) pilhaDeAvisos.remove();
    }

    prepararRascunhos();
    renderizarBarra();

    registrarServiceWorker();

    window.addEventListener('online', aoVoltarARede);
    window.addEventListener('offline', function () {
      renderizarBarra();
    });
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') sincronizar();
    });

    /* Uma tentativa logo depois de abrir (pode haver fila de ontem) e depois
       de tempos em tempos, para a rede que voltou sem avisar ninguém. */
    window.setTimeout(sincronizar, 1200);
    window.setInterval(function () {
      if (estaOnline()) sincronizar();
    }, INTERVALO_SINCRONIA);

    /* Sair limpa as telas guardadas no aparelho (o servidor não sabe disso). */
    document.addEventListener('submit', function (evento) {
      var form = evento.target;
      if (!form || form.tagName !== 'FORM' || !ehSaida(form)) return;
      if (navigator.serviceWorker && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ type: 'todonow:purge' });
      }
    });
  }

  window.TodonowOffline = {
    estaOnline: estaOnline,
    sincronizar: sincronizar,
    retentarTudo: retentarTudo,
    enfileirar: enfileirar,
    enfileirarFormulario: enfileirarFormulario,
    enviarOuEnfileirar: enviarOuEnfileirar,
    enviarOuEnfileirarJson: enviarOuEnfileirarJson,
    marcarPendente: marcarPendente,
    limparMarcasDePendencia: limparMarcasDePendencia,
    atualizarTela: atualizarTela,
    atualizarBarra: renderizarBarra,
    limparRascunho: limparRascunho,
    url: resolverUrl
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();
