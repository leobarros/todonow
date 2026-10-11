/* todonow — editor de anotações com dois modos.
 *
 *  Visual   : contenteditable (WYSIWYG) para quem não escreve Markdown;
 *  Markdown : textarea com a sintaxe, para quem prefere digitar.
 *
 * O textarea é sempre a fonte da verdade do formulário. Ao enviar no modo
 * visual, o HTML é convertido para Markdown com o Turndown — a conversão é
 * síncrona, então dá tempo de escrever no textarea antes do envio.
 *
 * Sem JavaScript nada disso roda: o textarea aparece sozinho e salva igual.
 */
(function () {
  'use strict';

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = csrfMeta ? csrfMeta.content : '';

  var SIMBOLOS = { ' ': '•', x: '✕', '-': '~', '>': '>', '<': '<', o: '○' };
  var CLASSES = {
    ' ': 'bujo-open',
    x: 'bujo-done is-struck',
    '-': 'bujo-irrelevant is-struck',
    '>': 'bujo-migrated',
    '<': 'bujo-scheduled',
    o: 'bujo-event'
  };

  function turndown() {
    if (typeof window.TurndownService !== 'function') return null;
    var servico = new window.TurndownService({
      headingStyle: 'atx',
      hr: '---',
      bulletListMarker: '-',
      codeBlockStyle: 'fenced',
      emDelimiter: '*'
    });

    if (window.turndownPluginGfm && window.turndownPluginGfm.gfm) {
      servico.use(window.turndownPluginGfm.gfm);
    }

    // O símbolo é enfeite: não entra no Markdown.
    servico.addRule('bujoSimbolo', {
      filter: function (no) {
        return no.nodeName === 'SPAN' && no.classList.contains('bujo-symbol');
      },
      replacement: function () {
        return '';
      }
    });

    // Itens BUJO voltam como "- [x] texto".
    servico.addRule('bujoItem', {
      filter: function (no) {
        return no.nodeName === 'LI' && no.hasAttribute('data-bujo');
      },
      replacement: function (conteudo, no) {
        var marca = no.getAttribute('data-bujo') || ' ';
        var prefixo =
          no.parentNode && no.parentNode.nodeName === 'OL' ? '1. [' + marca + '] ' : '- [' + marca + '] ';
        return prefixo + conteudo.replace(/\n+/g, ' ').trim() + '\n';
      }
    });

    return servico;
  }

  function htmlParaMarkdown(html) {
    var servico = turndown();
    if (!servico) return null;
    return servico
      .turndown(html || '')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function selecionar(textarea, inicio, fim) {
    textarea.focus();
    textarea.setSelectionRange(inicio, fim);
  }

  function envolver(textarea, molde) {
    var inicio = textarea.selectionStart;
    var fim = textarea.selectionEnd;
    var escolhido = textarea.value.slice(inicio, fim);
    var partes = molde.split('|');
    var antes = partes[0] || '';
    var depois = partes.length > 1 ? partes.slice(1).join('|') : '';

    textarea.setRangeText(antes + escolhido + depois, inicio, fim, 'end');
    if (!escolhido) selecionar(textarea, inicio + antes.length, inicio + antes.length);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function porLinha(textarea, molde) {
    var inicio = textarea.selectionStart;
    var fim = textarea.selectionEnd;
    var marca = molde.replace('|', '');
    var linhas = (textarea.value.slice(inicio, fim) || '').split('\n');

    var transformado = linhas
      .map(function (linha) {
        if (marca === '---') return linha + '\n\n---';
        return linha.startsWith(marca.trim() + ' ') || linha === marca.trim() ? linha : marca + linha;
      })
      .join('\n');

    textarea.setRangeText(transformado, inicio, fim, 'end');
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  /* O clique no botão tira o foco do conteúdo editável, então guardamos a última
     seleção e a devolvemos antes de aplicar qualquer formatação. */
  function guardarSelecao(editor) {
    var selecao = window.getSelection();
    if (selecao && selecao.rangeCount && editor.contains(selecao.anchorNode)) {
      editor._ultimaSelecao = selecao.getRangeAt(0).cloneRange();
    }
  }

  function restaurarSelecao(editor) {
    editor.focus();
    var selecao = window.getSelection();
    if (editor._ultimaSelecao && selecao) {
      selecao.removeAllRanges();
      selecao.addRange(editor._ultimaSelecao);
    }
    return selecao;
  }

  function focarNo(editor, elemento) {
    if (!elemento) return;
    var range = document.createRange();
    range.selectNodeContents(elemento);
    range.collapse(false);
    var selecao = window.getSelection();
    selecao.removeAllRanges();
    selecao.addRange(range);
    guardarSelecao(editor);
  }

  function blocoAtual(editor) {
    var selecao = window.getSelection();
    if (!selecao || !selecao.rangeCount) return null;
    var no = selecao.anchorNode;
    var elemento = no ? (no.nodeType === 1 ? no : no.parentElement) : null;
    if (!elemento || !editor.contains(elemento)) return null;
    var bloco = elemento.closest("p, li, h1, h2, h3, h4, h5, h6, blockquote, pre, div");
    return bloco && bloco !== editor ? bloco : null;
  }

  function avisar(editor) {
    editor.dispatchEvent(new Event('input', { bubbles: true }));
  }

  /* Formatação inline: envolve a seleção em <strong>, <em>, <del>, <code> ou <a>. */
  function envolverInline(editor, tag, atributos) {
    var selecao = restaurarSelecao(editor);
    if (!selecao || !selecao.rangeCount) return;
    var range = selecao.getRangeAt(0);
    if (range.collapsed) return; // sem seleção não há o que formatar

    var elemento = document.createElement(tag);
    Object.keys(atributos || {}).forEach(function (chave) {
      elemento.setAttribute(chave, atributos[chave]);
    });

    try {
      range.surroundContents(elemento);
    } catch (erro) {
      elemento.appendChild(range.extractContents());
      range.insertNode(elemento);
    }
    focarNo(editor, elemento);
    avisar(editor);
  }

  /* Blocos: transforma o parágrafo atual em título, citação… e volta se repetir. */
  function trocarBloco(editor, tag) {
    var bloco = blocoAtual(editor);

    // Dentro de um item de lista, o bloco novo entra depois da lista (senão o
    // <li> viraria um título solto dentro do <ul>).
    if (bloco && bloco.tagName.toLowerCase() === 'li') {
      var listaDono = bloco.closest('ul, ol') || bloco.parentElement;
      listaDono.insertAdjacentHTML('afterend', '<' + tag + '><br></' + tag + '>');
      focarNo(editor, listaDono.nextElementSibling);
      avisar(editor);
      return;
    }

    if (!bloco) {
      editor.insertAdjacentHTML('beforeend', '<' + tag + '><br></' + tag + '>');
      focarNo(editor, editor.lastElementChild);
      avisar(editor);
      return;
    }
    if (bloco.tagName.toLowerCase() === tag) {
      // toggles: volta a ser parágrafo
      var paragrafo = document.createElement('p');
      paragrafo.innerHTML = bloco.innerHTML;
      bloco.replaceWith(paragrafo);
      focarNo(editor, paragrafo);
      avisar(editor);
      return;
    }
    var novo = document.createElement(tag);
    novo.innerHTML = bloco.innerHTML;
    bloco.replaceWith(novo);
    focarNo(editor, novo);
    avisar(editor);
  }

  function alternarLista(editor, tag) {
    var bloco = blocoAtual(editor);
    if (bloco && bloco.tagName.toLowerCase() === 'li') {
      var lista = bloco.parentElement;
      if (lista.tagName.toLowerCase() === tag) {
        // mesmo tipo: desfaz a lista
        var paragrafo = document.createElement('p');
        paragrafo.innerHTML = bloco.innerHTML;
        if (lista.children.length === 1) {
          lista.replaceWith(paragrafo);
        } else {
          bloco.replaceWith(paragrafo);
        }
        focarNo(editor, paragrafo);
      } else {
        var convertida = document.createElement(tag);
        convertida.innerHTML = lista.innerHTML;
        lista.replaceWith(convertida);
        focarNo(editor, convertida);
      }
      avisar(editor);
      return;
    }

    var nova = document.createElement(tag);
    var item = document.createElement('li');
    item.innerHTML = bloco ? bloco.innerHTML : '<br>';
    nova.appendChild(item);
    if (bloco) bloco.replaceWith(nova);
    else editor.appendChild(nova);
    focarNo(editor, item);
    avisar(editor);
  }

  /* Insere um bloco novo depois do bloco atual (evita <ul> dentro de <p>). */
  function inserirBloco(editor, html) {
    restaurarSelecao(editor);
    var bloco = blocoAtual(editor);
    if (bloco) bloco.insertAdjacentHTML('afterend', html);
    else editor.insertAdjacentHTML('beforeend', html);

    var itens = editor.querySelectorAll('li[data-bujo]');
    if (itens.length) {
      var alvo = itens[itens.length - 1].querySelector('.bujo-text');
      if (alvo) {
        focarNo(editor, alvo);
        avisar(editor);
        return;
      }
    }
    var ultimo = editor.lastElementChild;
    focarNo(editor, ultimo);
    avisar(editor);
  }

  function itemBujo(marca) {
    var simbolo = SIMBOLOS[marca] || '•';
    var classes = CLASSES[marca] || 'bujo-open';
    return (
      '<li class="bujo-item ' +
      classes +
      '" data-bujo="' +
      marca +
      '"><span class="bujo-symbol">' +
      simbolo +
      '</span> <span class="bujo-text">atividade</span></li>'
    );
  }

  /* Acrescenta o item na lista BUJO existente (ou cria uma) — assim os itens
     ficam numa lista só, e o Markdown sai enxuto em vez de uma lista por item. */
  function inserirItemBujo(editor, marca) {
    restaurarSelecao(editor);
    var bloco = blocoAtual(editor);
    var lista = null;

    if (bloco && bloco.tagName.toLowerCase() === 'li') {
      var pai = bloco.closest('ul');
      if (pai && pai.querySelector('li[data-bujo]')) lista = pai;
    }
    if (!lista) {
      var anterior = bloco ? bloco.previousElementSibling : editor.lastElementChild;
      if (anterior && anterior.tagName === 'UL' && anterior.querySelector('li[data-bujo]')) {
        lista = anterior;
      }
    }
    if (!lista) {
      if (bloco) {
        bloco.insertAdjacentHTML('afterend', '<ul></ul>');
        lista = bloco.nextElementSibling;
      } else {
        editor.insertAdjacentHTML('beforeend', '<ul></ul>');
        lista = editor.lastElementChild;
      }
    }

    lista.insertAdjacentHTML('beforeend', itemBujo(marca));
    var alvo = lista.lastElementChild.querySelector('.bujo-text');
    focarNo(editor, alvo);
    avisar(editor);
  }

  function montar(raiz) {
    var textarea = raiz.querySelector('textarea');
    var visual = raiz.querySelector('[data-md-area]');
    var painelVisual = raiz.querySelector('[data-md-pane="visual"]');
    var painelMarkdown = raiz.querySelector('[data-md-pane="markdown"]');
    var url = raiz.dataset.renderUrl;
    if (!textarea || !visual || !painelVisual || !painelMarkdown) return;

    var modo = 'markdown';

    function pintarBotoes() {
      raiz.querySelectorAll('[data-md-mode]').forEach(function (botao) {
        var ativo = botao.dataset.mdMode === modo;
        botao.classList.toggle('active', ativo);
        botao.setAttribute('aria-pressed', ativo ? 'true' : 'false');
      });
    }

    function mostrarModo(novo) {
      modo = novo;
      painelVisual.hidden = novo !== 'visual';
      painelMarkdown.hidden = novo === 'visual';
      pintarBotoes();
    }

    /* Markdown -> Visual: quem renderiza é o servidor (mesmo pipeline da
       leitura), então o que você edita é o que vai aparecer depois de salvo. */
    function carregarVisual() {
      if (!url || !textarea.value.trim()) {
        visual.innerHTML = '';
        return Promise.resolve();
      }
      return fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF, Accept: 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ notes: textarea.value })
      })
        .then(function (resposta) {
          if (!resposta.ok) throw new Error('HTTP ' + resposta.status);
          return resposta.json();
        })
        .then(function (dados) {
          visual.innerHTML = (dados && dados.html) || '';
        });
    }

    function trocarPara(novo) {
      if (novo === modo) return;
      if (novo === 'markdown') {
        var markdown = htmlParaMarkdown(visual.innerHTML);
        if (markdown !== null) textarea.value = markdown;
        mostrarModo('markdown');
        return;
      }
      carregarVisual()
        .then(function () {
          mostrarModo('visual');
        })
        .catch(function () {
          mostrarModo('markdown'); // sem renderização, fica no texto puro
        });
    }

    raiz.querySelectorAll('[data-md-mode]').forEach(function (botao) {
      botao.addEventListener('click', function () {
        trocarPara(botao.dataset.mdMode);
      });
    });

    // O botão não pode roubar o foco do texto (senão a formatação perde a seleção).
    raiz.querySelectorAll('.md-toolbar button').forEach(function (botao) {
      botao.addEventListener('mousedown', function (evento) {
        if (modo === 'visual') evento.preventDefault();
      });
    });

    ['keyup', 'mouseup', 'input', 'blur'].forEach(function (evento) {
      visual.addEventListener(evento, function () {
        guardarSelecao(visual);
      });
    });

    /* Barra: age no modo ativo. */
    raiz.querySelectorAll('[data-md-visual], [data-md-insert], [data-md-block]').forEach(function (botao) {
      botao.addEventListener('click', function () {
        if (modo === 'markdown') {
          if (botao.dataset.mdInsert) envolver(textarea, botao.dataset.mdInsert);
          else if (botao.dataset.mdBlock) porLinha(textarea, botao.dataset.mdBlock);
          return;
        }

        restaurarSelecao(visual);
        var acao = botao.dataset.mdVisual || '';

        if (acao.indexOf('bujo:') === 0) {
          inserirItemBujo(visual, acao.slice(5));
          return;
        }
        switch (acao) {
          case 'bold':
            envolverInline(visual, 'strong');
            return;
          case 'italic':
            envolverInline(visual, 'em');
            return;
          case 'strikeThrough':
            envolverInline(visual, 'del');
            return;
          case 'code':
            envolverInline(visual, 'code');
            return;
          case 'link':
            var endereco = window.prompt('Endereço do link:', 'https://');
            if (endereco) envolverInline(visual, 'a', { href: endereco });
            return;
          case 'h2':
            trocarBloco(visual, 'h2');
            return;
          case 'insertUnorderedList':
            alternarLista(visual, 'ul');
            return;
          case 'insertOrderedList':
            alternarLista(visual, 'ol');
            return;
          case 'hr':
            inserirBloco(visual, '<hr><p><br></p>');
            return;
          default:
            if (acao.indexOf('blockquote') !== -1) {
              trocarBloco(visual, 'blockquote');
            }
        }
      });
    });

    /* Atalhos no modo visual. */
    visual.addEventListener('keydown', function (evento) {
      if (!(evento.ctrlKey || evento.metaKey)) return;
      var tecla = evento.key.toLowerCase();
      if (tecla === 'b') {
        evento.preventDefault();
        envolverInline(visual, 'strong');
      } else if (tecla === 'i') {
        evento.preventDefault();
        envolverInline(visual, 'em');
      }
    });

    /* Antes de enviar: no modo visual, converte e escreve no textarea. */
    var formulario = raiz.closest('form');
    if (formulario) {
      formulario.addEventListener('submit', function () {
        if (modo !== 'visual') return;
        var markdown = htmlParaMarkdown(visual.innerHTML);
        if (markdown !== null) textarea.value = markdown;
      });
    }

    /* Começa no visual: é o modo de quem não escreve Markdown. */
    trocarPara('visual');
  }

  function iniciar() {
    document.querySelectorAll('[data-md-editor]').forEach(montar);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();
