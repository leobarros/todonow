/* todonow — regras do modo offline que não dependem do DOM.
 *
 * São as decisões em que um erro só apareceria em produção, com a rede caindo
 * no meio do caminho: quanto esperar antes de tentar de novo, quando duas
 * ações da mesma tarefa se anulam, quando o problema é sessão (e não conteúdo)
 * e quando um rascunho pode voltar sem atropelar o que está no servidor.
 *
 * Ficam separadas de js/offline.js para poderem ser conferidas direto no Node
 * (tests/js/offline_harness.mjs), sem simular navegador.
 */
(function (raiz) {
  'use strict';

  var ESPERA_BASE = 2000;
  var ESPERA_MAXIMA = 60000;
  var TETO_DE_EXPOENTE = 5;

  /* Só estes tipos *definem um valor* numa tarefa que já existe, e por isso
     podem ser trocados pelo último pedido. Criar (quick-add) nunca está aqui:
     duas criações são duas tarefas, não a mesma duas vezes. */
  var TIPOS_QUE_SE_ANULAM = ['status', 'migrate', 'reschedule'];

  /** Espera crescente entre tentativas, com teto. */
  function esperaDe(attempts) {
    var tentativas = Math.max(0, parseInt(attempts, 10) || 0);
    return Math.min(ESPERA_MAXIMA, ESPERA_BASE * Math.pow(2, Math.min(tentativas, TETO_DE_EXPOENTE)));
  }

  /**
   * A ação nova substitui a antiga?
   *
   * Só quando as duas mexem na **mesma tarefa existente** e definem o mesmo
   * tipo de valor: adiar duas vezes é a última data; mudar a situação duas
   * vezes é a última situação. Nada disso vale para tarefas diferentes nem
   * para criação — anular uma criação apagaria uma tarefa do usuário.
   */
  function deveSubstituir(outra, nova) {
    if (!outra || !nova) return false;
    if (outra.state !== 'pending') return false;
    /* Remover torna inútil qualquer outra pendência daquela tarefa. */
    if (nova.kind === 'delete') return true;
    if (!nova.taskId) return false;
    if (TIPOS_QUE_SE_ANULAM.indexOf(nova.kind) === -1) return false;
    return outra.kind === nova.kind;
  }

  /**
   * O servidor recusou porque a sessão não vale mais?
   *
   * Distinguir isto de um erro de conteúdo é o que decide entre "tente de novo
   * depois de entrar" e "esta alteração nunca vai passar, avise o usuário".
   */
  function pareceProblemaDeSessao(resposta, dados) {
    if (!resposta) return false;
    if (resposta.status === 401 || resposta.status === 403) return true;
    // login_required redireciona a navegação para a tela de entrada.
    if (resposta.redirected && String(resposta.url || '').indexOf('/auth/login') !== -1) return true;
    var mensagem = dados && dados.error ? String(dados.error) : '';
    return /sess[ãa]o|expirou|entre novamente|recarregue a p[áa]gina/i.test(mensagem);
  }

  /** Texto legível para o que o servidor recusou. */
  function mensagemDeErro(dados) {
    if (!dados) return '';
    if (typeof dados.error === 'string') return dados.error;
    if (dados.errors && typeof dados.errors === 'object') {
      var partes = [];
      Object.keys(dados.errors).forEach(function (campo) {
        var lista = dados.errors[campo];
        if (Object.prototype.toString.call(lista) === '[object Array]') {
          partes = partes.concat(lista);
        } else if (lista) {
          partes.push(String(lista));
        }
      });
      if (partes.length) return partes.join(' ');
    }
    return '';
  }

  /**
   * O que fazer com um rascunho guardado?
   *
   *  * 'nada'      — não existe rascunho (ou é velho demais);
   *  * 'perguntar' — o formulário chegou a ser enviado e nunca vimos a
   *                  confirmação: só o usuário sabe se deu certo;
   *  * 'restaurar' — a tela está exatamente como quando o rascunho foi feito,
   *                  então o texto digitado é o que falta;
   *  * 'descartar' — o servidor mudou o conteúdo desde então: o dado atual
   *                  manda, e trazer o rascunho atropelaria a mudança.
   */
  function decisaoDeRascunho(salvo, atual) {
    if (!salvo || !salvo.values) return 'nada';
    if (salvo.submittedAt) return 'perguntar';
    if (JSON.stringify(salvo.baseline) !== JSON.stringify(atual)) return 'descartar';
    return 'restaurar';
  }

  /**
   * Este formulário pode entrar na fila do modo offline?
   *
   * Ficam de fora: o que o servidor precisa responder na hora (entrar, sair,
   * backup), o que já tem tratamento próprio por AJAX, o que não é POST e o
   * que carrega arquivo — arquivo não cabe numa fila de JSON e sumiria sem
   * aviso.
   */
  function deveEnfileirar(opcoes) {
    var dados = opcoes || {};
    if (dados.marcado === 'off' || dados.marcado === 'ajax') return false;
    if (String(dados.metodo || 'get').toLowerCase() !== 'post') return false;
    if (!dados.mesmaOrigem) return false;
    var caminho = String(dados.caminho || '');
    if (caminho.indexOf('/auth/') === 0 || caminho.indexOf('/backup/') === 0) return false;
    if (dados.temArquivo) return false;
    return true;
  }

  /** Assinatura curta e estável de um texto (identidade do formulário). */
  function assinatura(texto) {
    var hash = 0;
    var valor = String(texto || '');
    for (var i = 0; i < valor.length; i++) {
      hash = (hash * 31 + valor.charCodeAt(i)) | 0;
    }
    return (hash >>> 0).toString(36);
  }

  raiz.TodonowOfflineCore = {
    esperaDe: esperaDe,
    deveSubstituir: deveSubstituir,
    pareceProblemaDeSessao: pareceProblemaDeSessao,
    mensagemDeErro: mensagemDeErro,
    decisaoDeRascunho: decisaoDeRascunho,
    deveEnfileirar: deveEnfileirar,
    assinatura: assinatura,
    ESPERA_BASE: ESPERA_BASE,
    ESPERA_MAXIMA: ESPERA_MAXIMA
  };
})(typeof window !== 'undefined' ? window : globalThis);
