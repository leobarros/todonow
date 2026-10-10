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
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.classList.remove('d-none');
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
        return response.json();
      })
      .then(function (data) {
        hideError();
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
      fetch(moveUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CSRF,
          Accept: 'application/json'
        },
        credentials: 'same-origin',
        body: JSON.stringify({ id: info.event.extendedProps.taskId || info.event.id, date: date })
      })
        .then(function (response) {
          if (!response.ok) throw new Error('HTTP ' + response.status);
          return response.json();
        })
        .then(function (data) {
          if (!data.ok) throw new Error(data.error || 'erro desconhecido');
          hideError();
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
