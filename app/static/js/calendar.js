/* todonow — FullCalendar integration (feed, filters, drag & drop). */
(function () {
  'use strict';

  var el = document.getElementById('calendar');
  if (!el || typeof window.FullCalendar === 'undefined') return;

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = csrfMeta ? csrfMeta.content : '';
  var feedUrl = el.dataset.feed;
  var moveUrl = el.dataset.move;
  var errorBox = document.getElementById('calendar-error');
  var filters = { status: '', kind: '', show_due: '1' };
  var VIEW_KEY = 'todonow-cal-view';

  function showError(message) {
    showMessage(message, 'danger');
  }

  function showMessage(message, tom) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.classList.remove('d-none', 'alert-danger', 'alert-warning', 'alert-info');
    errorBox.classList.add('alert-' + tom);
  }

  function hideError() {
    if (!errorBox) return;
    errorBox.classList.add('d-none');
  }

  function fetchEvents(info, success, failure) {
    var params = new URLSearchParams({
      start: info.startStr,
      end: info.endStr,
      status: filters.status,
      kind: filters.kind,
      show_due: filters.show_due
    });
    fetch(feedUrl + '?' + params.toString(), {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin'
    })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        // O service worker marca assim quando o JSON veio do cache local.
        if (response.headers.get('X-Todonow-Offline') === '1') {
          showMessage('Sem conexão: mostrando as tarefas do último acesso.', 'warning');
        } else {
          hideError();
        }
        return response.json();
      })
      .then(function (data) {
        success(data);
      })
      .catch(function (error) {
        showError('Não foi possível carregar as tarefas do calendário (' + error.message + ').');
        if (failure) failure(error);
        else success([]);
      });
  }

  var calendar = new window.FullCalendar.Calendar(el, {
    locale: 'pt-br',
    initialView: localStorage.getItem(VIEW_KEY) || 'dayGridMonth',
    firstDay: parseInt(el.dataset.weekStart || '0', 10),
    height: 'auto',
    expandRows: true,
    navLinks: true,
    nowIndicator: true,
    dayMaxEvents: 4,
    fixedWeekCount: false,
    headerToolbar: {
      left: 'prev,next today',
      center: 'title',
      right: 'dayGridMonth,timeGridWeek,listWeek'
    },
    buttonText: {
      today: 'Hoje',
      month: 'Mês',
      week: 'Semana',
      list: 'Lista'
    },
    titleFormat: { year: 'numeric', month: 'long', day: 'numeric' },
    eventTimeFormat: { hour: '2-digit', minute: '2-digit', hour12: false },
    events: fetchEvents,
    editable: true,
    eventStartEditable: true,
    eventDurationEditable: false,
    eventClick: function (info) {
      // Let the browser follow the event URL (task detail page).
      return true;
    },
    eventDidMount: function (info) {
      var props = info.event.extendedProps || {};
      var parts = [info.event.title];
      if (props.collection) parts.push('Coleção: ' + props.collection);
      if (props.notes) parts.push(String(props.notes).slice(0, 240));
      if (info.event.url) parts.push('Clique para abrir os detalhes.');
      info.el.setAttribute('title', parts.join('\n'));
    },
    eventDrop: function (info) {
      var date = info.event.startStr.slice(0, 10);
      var taskId = info.event.extendedProps.taskId || info.event.id;
      var mover = function (url, corpo, rotulo) {
        // Sem rede o arrasto continua valendo: a data vai absoluta (12/03) e a
        // ação entra na fila, em vez de o cartão voltar sozinho para o lugar.
        if (!window.TodonowOffline) {
          return fetch(url, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': CSRF,
              Accept: 'application/json'
            },
            credentials: 'same-origin',
            body: JSON.stringify(corpo)
          }).then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
          });
        }
        return window.TodonowOffline.enviarOuEnfileirarJson(url, corpo, {
          kind: 'reschedule',
          taskId: taskId,
          label: rotulo
        });
      };

      mover(moveUrl, { id: taskId, date: date }, 'Mover para ' + date + ': ' + info.event.title)
        .then(function (resultado) {
          if (resultado.estado === 'invalido') {
            info.revert();
            showError('Não foi possível reagendar: ' + ((resultado.dados && resultado.dados.error) || 'recusado'));
            return;
          }
          if (resultado.dados && resultado.dados.ok === false) {
            info.revert();
            showError('Não foi possível reagendar: ' + (resultado.dados.error || 'erro desconhecido'));
            return;
          }
          if (resultado.estado === 'na-fila') {
            showMessage('Sem conexão: o novo dia foi guardado e será enviado quando a rede voltar.', 'warning');
          } else {
            hideError();
          }
        })
        .catch(function (error) {
          info.revert();
          showError('Não foi possível reagendar: ' + error.message);
        });
    },
    datesSet: function (info) {
      try {
        localStorage.setItem(VIEW_KEY, info.view.type);
      } catch (e) {
        /* ignore */
      }
    },
    noEventsContent: 'Nenhuma entrada neste período.'
  });

  calendar.render();

  /* A fila do modo offline avisa quando terminou de enviar: aí o calendário
     recarrega para mostrar o que o servidor realmente gravou. */
  document.addEventListener('todonow:refresh', function () {
    calendar.refetchEvents();
  });

  /* ---- filters ---- */
  document.querySelectorAll('[data-cal-filter]').forEach(function (button) {
    button.addEventListener('click', function () {
      var group = button.dataset.calFilter;
      document
        .querySelectorAll('[data-cal-filter="' + group + '"]')
        .forEach(function (sibling) {
          sibling.classList.toggle('active', sibling === button);
        });
      filters[group] = button.dataset.value || '';
      calendar.refetchEvents();
    });
  });

  var dueSwitch = document.getElementById('cal-show-due');
  if (dueSwitch) {
    dueSwitch.addEventListener('change', function () {
      filters.show_due = dueSwitch.checked ? '1' : '0';
      calendar.refetchEvents();
    });
  }
})();
