/* todonow — global UI behaviours (progressive enhancement only). */
(function () {
  'use strict';

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = csrfMeta ? csrfMeta.content : '';

  /* ------------------------------------------------------------------
   * Confirmação em modal Bootstrap (segue claro/escuro), no lugar do
   * window.confirm nativo que quebrava o visual do app.
   *
   * Uso nos templates:
   *   <form data-confirm="Remover a tarefa X?">                  (no form)
   *   <button data-confirm="..." data-confirm-label="Sim, remover"
   *           data-confirm-note="A tarefa será apagada permanentemente.">
   * ------------------------------------------------------------------ */
  var confirmEl = document.getElementById('confirm-modal');
  var confirmModal =
    confirmEl && window.bootstrap && window.bootstrap.Modal ? new window.bootstrap.Modal(confirmEl) : null;
  var confirmState = { form: null, submitter: null };

  function submitConfirmed(form, submitter) {
    if (!form) return;
    // Marca para o handler abaixo não perguntar de novo.
    form.dataset.confirmed = '1';
    try {
      if (typeof form.requestSubmit === 'function') {
        form.requestSubmit(submitter && form.contains(submitter) ? submitter : undefined);
      } else {
        form.submit();
      }
    } finally {
      delete form.dataset.confirmed;
    }
  }

  function askConfirm(message, note, label, form, submitter) {
    if (!confirmModal) {
      // Sem o bundle do Bootstrap: cai no diálogo nativo, mas ainda confirma.
      if (window.confirm(note ? message + '\n\n' + note : message)) {
        submitConfirmed(form, submitter);
      }
      return;
    }

    document.getElementById('confirm-modal-message').textContent = message;

    var noteEl = document.getElementById('confirm-modal-note');
    if (note) {
      noteEl.textContent = note;
      noteEl.classList.remove('d-none');
    } else {
      noteEl.textContent = '';
      noteEl.classList.add('d-none');
    }

    var okButton = document.getElementById('confirm-modal-ok');
    okButton.textContent = '';
    var icon = document.createElement('i');
    icon.className = 'bi bi-check2 me-1';
    okButton.appendChild(icon);
    okButton.appendChild(document.createTextNode(label));

    confirmState.form = form;
    confirmState.submitter = submitter;
    confirmModal.show();

    // Foco no "Cancelar": a ação destrutiva nunca é a padrão.
    confirmEl.addEventListener(
      'shown.bs.modal',
      function () {
        var cancel = document.getElementById('confirm-modal-cancel');
        if (cancel) cancel.focus();
      },
      { once: true }
    );
  }

  if (confirmEl) {
    document.getElementById('confirm-modal-ok').addEventListener('click', function () {
      var state = confirmState;
      confirmState = { form: null, submitter: null };
      confirmModal.hide();
      submitConfirmed(state.form, state.submitter);
    });
  }

  document.addEventListener(
    'submit',
    function (event) {
      var form = event.target;
      if (!form || form.tagName !== 'FORM') return;
      if (form.dataset.confirmed === '1') return; // já confirmado no modal

      var submitter = event.submitter && form.contains(event.submitter) ? event.submitter : null;
      // Os atributos podem estar no formulário ou no botão que enviou; o botão
      // vence. (Um dataset vazio é "truthy", então nada de escolher um só.)
      var buttonData = (submitter && submitter.dataset) || {};
      var formData = form.dataset;
      var message = buttonData.confirm || formData.confirm;
      if (!message) return;

      event.preventDefault();
      event.stopImmediatePropagation();
      askConfirm(
        message,
        buttonData.confirmNote || formData.confirmNote || '',
        buttonData.confirmLabel || formData.confirmLabel || 'Confirmar',
        form,
        submitter
      );
    },
    true
  );

  /* ------------------------------------------------------------------
   * Show/hide password
   * ------------------------------------------------------------------ */
  document.addEventListener('click', function (event) {
    var button = event.target.closest('.js-reveal-password');
    if (!button) return;
    var wrapper = button.closest('.input-group') || button.parentElement;
    var input = wrapper ? wrapper.querySelector('input') : null;
    if (!input) return;
    var toText = input.type === 'password';
    input.type = toText ? 'text' : 'password';
    var icon = button.querySelector('i');
    if (icon) {
      icon.classList.toggle('bi-eye', !toText);
      icon.classList.toggle('bi-eye-slash', toText);
    }
  });

  /* ------------------------------------------------------------------
   * Date shortcuts in the task form
   * ------------------------------------------------------------------ */
  function fieldByName(name) {
    return document.querySelector('input[name="' + name + '"]');
  }

  document.addEventListener('click', function (event) {
    var setter = event.target.closest('.js-set-date');
    if (setter) {
      var input = fieldByName(setter.dataset.target);
      if (input) {
        input.value = setter.dataset.value;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
      return;
    }
    var clearer = event.target.closest('.js-clear-date');
    if (clearer) {
      var target = fieldByName(clearer.dataset.target);
      if (target) {
        target.value = '';
        target.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }
  });

  /* ------------------------------------------------------------------
   * Colour mode
   * ------------------------------------------------------------------ */
  function applyTheme(theme) {
    var resolved = theme;
    if (theme === 'auto') {
      resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-bs-theme', resolved);
    return resolved;
  }

  var themeButton = document.getElementById('theme-toggle');
  if (themeButton) {
    themeButton.addEventListener('click', function () {
      var current = document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light';
      var next = current === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      try {
        localStorage.setItem('todonow-theme', next);
      } catch (e) {
        /* storage unavailable — ignore */
      }
      var url = themeButton.dataset.themeUrl;
      if (!url) return;
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF, Accept: 'application/json' },
        body: JSON.stringify({ theme: next }),
        credentials: 'same-origin'
      }).catch(function () {
        /* offline is fine: the browser preference still applies */
      });
    });
  }

  /* ------------------------------------------------------------------
   * Inline completion toggle: strikes the task through without a reload.
   * ------------------------------------------------------------------ */
  function symbolClasses(task) {
    var classes = ['bujo-symbol', 'bujo-symbol-' + task.status];
    if (task.kind && task.kind !== 'task') {
      classes.push('bujo-symbol-' + task.kind);
    }
    return classes.join(' ');
  }

  function patchRow(row, task) {
    if (!row) return;
    var closed = task.status === 'done' || task.status === 'cancelled';
    // Estado exato (atrasada, contadores, seções) vem da região viva logo depois.
    row.classList.toggle('is-done', closed);
    var symbol = row.querySelector('.bujo-symbol');
    if (symbol) {
      symbol.className = symbolClasses(task);
      symbol.textContent = task.bujo_symbol;
    }
    var button = row.querySelector('.bujo-check');
    if (button) {
      var label = task.status === 'done' ? 'Reabrir tarefa' : 'Concluir tarefa';
      button.setAttribute('aria-label', label);
      button.setAttribute('title', label);
      button.setAttribute('aria-pressed', task.status === 'done' ? 'true' : 'false');
    }
  }

  /* ------------------------------------------------------------------
   * Regiões vivas: depois de concluir/reabrir uma tarefa, o servidor é
   * consultado de novo e só os blocos marcados com data-live-region são
   * substituídos (contadores, barra de progresso, listas Hoje/Concluídas).
   * Assim nada fica desatualizado esperando um F5, e o formulário rápido
   * — que fica fora das regiões — não perde o que foi digitado.
   * ------------------------------------------------------------------ */
  var refreshRunning = false;
  var refreshQueued = false;

  function refreshLiveRegions() {
    var regions = Array.prototype.slice.call(document.querySelectorAll('[data-live-region]'));
    if (!regions.length) return;
    if (refreshRunning) {
      refreshQueued = true;
      return;
    }
    refreshRunning = true;

    fetch(window.location.href, { headers: { Accept: 'text/html' }, credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.text();
      })
      .then(function (html) {
        var fresh = new DOMParser().parseFromString(html, 'text/html');
        regions.forEach(function (region) {
          var key = region.getAttribute('data-live-region');
          var updated = fresh.querySelector('[data-live-region="' + key + '"]');
          if (updated) region.innerHTML = updated.innerHTML;
        });
      })
      .catch(function () {
        /* Sem alarde: se falhar, um F5 mostra o estado correto. */
      })
      .finally(function () {
        refreshRunning = false;
        if (refreshQueued) {
          refreshQueued = false;
          refreshLiveRegions();
        }
      });
  }

  /* ------------------------------------------------------------------
   * Apoio às ações que precisam sobreviver a uma rede instável.
   *
   * `enviar` tenta agora e, se não der, guarda na fila do modo offline
   * (js/offline.js). Devolve { estado: 'enviado' | 'na-fila' | 'invalido' }.
   * ------------------------------------------------------------------ */
  function offlineApi() {
    return window.TodonowOffline || null;
  }

  function enviar(form, opcoes) {
    var api = offlineApi();
    if (!api || !form) return Promise.resolve({ estado: 'invalido', dados: null });
    return api.enviarOuEnfileirar(form, opcoes);
  }

  function tituloDaLinha(row) {
    if (!row) return '';
    var titulo = row.querySelector('.task-title');
    return ((titulo ? titulo.textContent : '') || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  }

  function estadoOtimista(row, destino) {
    var simbolos = { task: '•', event: '○', note: '–' };
    var kind = (row && row.getAttribute('data-task-kind')) || 'task';
    return {
      status: destino,
      kind: kind,
      bujo_symbol: destino === 'done' ? '✕' : simbolos[kind] || '•'
    };
  }

  function somarDias(iso, dias) {
    var partes = String(iso || '').split('-');
    if (partes.length !== 3) return null;
    var data = new Date(Date.UTC(parseInt(partes[0], 10), parseInt(partes[1], 10) - 1, parseInt(partes[2], 10)));
    data.setUTCDate(data.getUTCDate() + dias);
    return data.toISOString().slice(0, 10);
  }

  /** Mensagem passageira no topo, no mesmo estilo dos avisos do servidor. */
  function aviso(mensagem, tom) {
    var area = document.querySelector('.flash-stack');
    if (!area) {
      area = document.createElement('div');
      area.className = 'flash-stack';
      var principal = document.getElementById('main');
      if (principal) principal.insertBefore(area, principal.firstChild);
      else document.body.appendChild(area);
    }
    var icones = {
      success: 'bi-check-circle',
      danger: 'bi-exclamation-octagon',
      warning: 'bi-exclamation-triangle',
      info: 'bi-info-circle'
    };
    var caixa = document.createElement('div');
    caixa.className = 'alert alert-' + tom + ' alert-dismissible fade show shadow-sm';
    caixa.setAttribute('role', 'alert');
    caixa.innerHTML = '<i class="bi ' + (icones[tom] || icones.info) + ' me-2"></i>';
    caixa.appendChild(document.createTextNode(mensagem));
    var fechar = document.createElement('button');
    fechar.type = 'button';
    fechar.className = 'btn-close';
    fechar.setAttribute('data-bs-dismiss', 'alert');
    fechar.setAttribute('aria-label', 'Fechar');
    caixa.appendChild(fechar);
    area.appendChild(caixa);

    window.setTimeout(function () {
      caixa.classList.remove('show');
      window.setTimeout(function () {
        caixa.remove();
      }, 200);
    }, tom === 'success' || tom === 'info' ? 2500 : 6000);
  }

  function limparErros(form) {
    Array.prototype.forEach.call(form.querySelectorAll('.is-invalid'), function (campo) {
      campo.classList.remove('is-invalid');
    });
    Array.prototype.forEach.call(form.querySelectorAll('.offline-erro'), function (caixa) {
      caixa.remove();
    });
  }

  function mostrarErros(form, erros) {
    limparErros(form);
    Object.keys(erros || {}).forEach(function (nome) {
      var campo = form.querySelector('[name="' + nome + '"]');
      var lista = erros[nome];
      var texto = Object.prototype.toString.call(lista) === '[object Array]' ? lista.join(' ') : String(lista);
      if (campo) {
        campo.classList.add('is-invalid');
        var caixa = document.createElement('div');
        caixa.className = 'invalid-feedback d-block offline-erro';
        caixa.textContent = texto;
        campo.parentNode.appendChild(caixa);
      } else {
        aviso(texto, 'danger');
      }
    });
    var primeiro = form.querySelector('.is-invalid');
    if (primeiro) primeiro.focus();
  }

  /* Delegação: os formulários que chegam junto com uma região atualizada
     continuam funcionando sem precisar re-registrar listeners. */
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form || !form.classList || !form.classList.contains('js-toggle-task')) return;
    if (form.dataset.ajax === 'off') return;

    event.preventDefault();
    var row = form.closest('[data-task-id]');
    var button = form.querySelector('button[type="submit"]');
    if (button) button.disabled = true;

    // A situação vai como valor **absoluto** ("done"), nunca como inversão:
    // dois cliques seguidos, ou um reenvio do modo offline, não podem desfazer
    // o primeiro. O campo escondido do formulário já traz o alvo.
    var destino = form.dataset.statusNext || (form.dataset.taskStatus === 'done' ? 'open' : 'done');
    var url = form.dataset.statusUrl || form.action;

    enviar(form, {
      url: url,
      kind: 'status',
      taskId: row ? parseInt(row.getAttribute('data-task-id'), 10) : null,
      label: (destino === 'done' ? 'Concluir: ' : 'Reabrir: ') + tituloDaLinha(row)
    })
      .then(function (resultado) {
        if (resultado.estado === 'invalido') {
          // O servidor recusou (sessão expirada, por exemplo): deixa o envio
          // normal do navegador mostrar a mensagem dele.
          form.dataset.ajax = 'off';
          form.submit();
          return;
        }
        if (resultado.estado === 'na-fila') {
          // Sem confirmação do servidor: riscamos assim mesmo, para o app
          // responder na hora, e a barra mostra o que está pendente.
          patchRow(row, estadoOtimista(row, destino));
        } else if (resultado.dados && resultado.dados.task) {
          patchRow(row, resultado.dados.task);
        }
        refreshLiveRegions();
      })
      .finally(function () {
        if (button) button.disabled = false;
      });
  });

  /* ------------------------------------------------------------------
   * Adicionar tarefa (formulário rápido)
   *
   * Online: envia e atualiza as listas sem recarregar. Sem rede (ou se a rede
   * falhar no meio), a tarefa fica na fila do modo offline em vez de virar uma
   * tela de erro — e o que estava digitado não se perde.
   * ------------------------------------------------------------------ */
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form || !form.classList || !form.classList.contains('js-quick-add')) return;
    if (form.dataset.ajax === 'off') return;

    event.preventDefault();
    var campo = form.querySelector('[name="title"]');
    var botao = form.querySelector('button[type="submit"]');
    if (botao) botao.disabled = true;

    enviar(form, {
      kind: 'quick-add',
      taskId: null,
      label: 'Nova tarefa: ' + ((campo && campo.value.trim()) || 'sem título')
    })
      .then(function (resultado) {
        if (resultado.estado === 'invalido') {
          if (resultado.dados && resultado.dados.errors) {
            mostrarErros(form, resultado.dados.errors);
          } else {
            form.dataset.ajax = 'off';
            form.submit();
          }
          return;
        }
        if (campo) campo.value = '';
        limparErros(form);
        if (resultado.estado === 'na-fila') {
          aviso('Sem conexão: a tarefa ficou guardada e será criada quando a rede voltar.', 'warning');
        } else {
          aviso('Tarefa adicionada.', 'success');
        }
        refreshLiveRegions();
      })
      .finally(function () {
        if (botao) botao.disabled = false;
      });
  });

  /* ------------------------------------------------------------------
   * Adiar (migração BUJO)
   *
   * A data de destino é calculada **agora** e vai junto com o pedido. Guardar
   * "próximo dia" na fila e resolver depois daria outra data — a tarefa já
   * teria mudado de dia quando o reenvio acontecesse.
   * ------------------------------------------------------------------ */
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form || !form.classList || !form.classList.contains('js-migrate')) return;
    if (form.dataset.ajax === 'off') return;

    event.preventDefault();
    var campos = {};
    new FormData(form).forEach(function (valor, nome) {
      if (typeof valor === 'string') campos[nome] = valor;
    });
    delete campos.preset;
    if (!campos.target_date) {
      var deslocamentos = { next_day: 1, next_week: 7, next_month: 30 };
      var preset = new FormData(form).get('preset') || 'next_day';
      campos.target_date = somarDias(form.dataset.migrateBase, deslocamentos[preset] || 1);
    }

    var row = form.closest('[data-task-id]');
    var botao = form.querySelector('button[type="submit"]');
    if (botao) botao.disabled = true;

    enviar(form, {
      kind: 'migrate',
      taskId: row ? parseInt(row.getAttribute('data-task-id'), 10) : null,
      label: 'Adiar: ' + tituloDaLinha(row),
      campos: campos
    })
      .then(function (resultado) {
        if (resultado.estado === 'invalido') {
          form.dataset.ajax = 'off';
          form.submit();
          return;
        }
        aviso(resultado.estado === 'na-fila' ? 'Adiamento guardado — sai quando a rede voltar.' : 'Tarefa adiada.', 'info');
        refreshLiveRegions();
      })
      .finally(function () {
        if (botao) botao.disabled = false;
      });
  });

  /* ------------------------------------------------------------------
   * Remover tarefa
   * ------------------------------------------------------------------ */
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form || !form.classList || !form.classList.contains('js-delete-task')) return;
    if (form.dataset.ajax === 'off') return;

    event.preventDefault();
    var row = form.closest('[data-task-id]');
    var botao = form.querySelector('button[type="submit"]');
    if (botao) botao.disabled = true;

    enviar(form, {
      kind: 'delete',
      taskId: row ? parseInt(row.getAttribute('data-task-id'), 10) : null,
      label: 'Remover: ' + tituloDaLinha(row)
    })
      .then(function (resultado) {
        if (resultado.estado === 'invalido') {
          form.dataset.ajax = 'off';
          form.submit();
          return;
        }
        if (resultado.estado === 'enviado' && row) row.remove();
        aviso(
          resultado.estado === 'na-fila' ? 'Remoção guardada — sai quando a rede voltar.' : 'Tarefa removida.',
          'info'
        );
        refreshLiveRegions();
      })
      .finally(function () {
        if (botao) botao.disabled = false;
      });
  });

  /* ------------------------------------------------------------------
   * Auto-submit filter switches on the task list
   * ------------------------------------------------------------------ */
  var filterForm = document.getElementById('task-filters');
  if (filterForm) {
    filterForm.querySelectorAll('select, input[type="checkbox"]').forEach(function (input) {
      input.addEventListener('change', function () {
        filterForm.submit();
      });
    });
  }

  /* O modo offline (js/offline.js) precisa destas duas para reconciliar a tela
     depois de enviar a fila: a região viva traz o estado real do servidor. */
  window.Todonow = window.Todonow || {};
  window.Todonow.refreshLiveRegions = refreshLiveRegions;
  window.Todonow.patchRow = patchRow;
})();
