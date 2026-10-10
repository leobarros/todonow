/* todonow — global UI behaviours (progressive enhancement only). */
(function () {
  'use strict';

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = csrfMeta ? csrfMeta.content : '';

  /* ------------------------------------------------------------------
   * Confirmation dialogs: <form data-confirm="..."> or a submit button
   * carrying data-confirm.
   * ------------------------------------------------------------------ */
  document.addEventListener(
    'submit',
    function (event) {
      var form = event.target;
      if (!form || form.tagName !== 'FORM') return;
      var message = (event.submitter && event.submitter.dataset && event.submitter.dataset.confirm) || form.dataset.confirm;
      if (message && !window.confirm(message)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
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
    row.classList.toggle('is-done', closed);
    row.classList.toggle('is-overdue', task.status === 'open' && row.dataset.overdue === '1');
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

  document.querySelectorAll('form.js-toggle-task').forEach(function (form) {
    form.addEventListener('submit', function (event) {
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
          if (data && data.task) patchRow(row, data.task);
        })
        .catch(function () {
          form.dataset.ajax = 'off';
          form.submit();
        })
        .finally(function () {
          if (button) button.disabled = false;
        });
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
