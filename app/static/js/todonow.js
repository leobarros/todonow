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

    fetch(form.action, {
      method: 'POST',
      body: new FormData(form),
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': CSRF, Accept: 'application/json' },
      credentials: 'same-origin'
    })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      })
      .then(function (data) {
        // Risca na hora (resposta imediata) e em seguida acerta as seções.
        if (data && data.task) patchRow(row, data.task);
        refreshLiveRegions();
      })
      .catch(function () {
        form.dataset.ajax = 'off';
        form.submit();
      })
      .finally(function () {
        if (button) button.disabled = false;
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
})();
