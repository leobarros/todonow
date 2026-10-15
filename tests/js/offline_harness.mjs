/* Harness de app/static/js/offline-core.js, rodado pelo pytest via node.
 *
 * São as regras que decidem o comportamento do modo offline quando algo dá
 * errado: quanto esperar, o que anula o quê, quando o problema é sessão e
 * quando um rascunho pode voltar. Nada aqui precisa de DOM.
 *
 * Uso: node offline_harness.mjs /caminho/para/offline-core.js
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const fonte = readFileSync(process.argv[2], 'utf8');
const sandbox = { console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fonte, sandbox, { filename: 'offline-core.js' });

const core = sandbox.TodonowOfflineCore;

let falhas = 0;
let total = 0;

function conferir(descricao, condicao, detalhe) {
  total++;
  if (condicao) {
    console.log('  ok   ' + descricao);
  } else {
    falhas++;
    console.log('  FALHA ' + descricao + (detalhe !== undefined ? ' → ' + JSON.stringify(detalhe) : ''));
  }
}

console.log('offline-core.js — regras do modo offline\n');

// ------------------------------------------------------------------
// Espera entre tentativas
// ------------------------------------------------------------------
console.log('espera crescente entre tentativas');
conferir('primeira tentativa espera pouco', core.esperaDe(1) === 4000, core.esperaDe(1));
conferir('cresce a cada tentativa', core.esperaDe(2) > core.esperaDe(1));
conferir('nunca passa do teto', core.esperaDe(50) === core.ESPERA_MAXIMA, core.esperaDe(50));
conferir('aguenta valor inválido', core.esperaDe(undefined) === core.ESPERA_BASE, core.esperaDe(undefined));
conferir('aguenta texto', core.esperaDe('3') === core.esperaDe(3));

// ------------------------------------------------------------------
// Anulação de ações repetidas na mesma tarefa
// ------------------------------------------------------------------
console.log('\nanulação de ações repetidas na mesma tarefa');
const pendente = (kind, taskId = 7) => ({ kind, taskId, state: 'pending' });

conferir('adiar duas vezes: vale a última', core.deveSubstituir(pendente('migrate'), pendente('migrate')));
conferir('mudar de situação duas vezes: vale a última',
  core.deveSubstituir(pendente('status'), pendente('status')));
conferir('adiar e concluir convivem (a ordem importa)',
  !core.deveSubstituir(pendente('migrate'), pendente('status')));
conferir('remover anula qualquer pendência da tarefa',
  core.deveSubstituir(pendente('status'), pendente('delete')));
conferir('remover anula até um adiamento', core.deveSubstituir(pendente('migrate'), pendente('delete')));
conferir('criar tarefa nunca é anulado (cada uma é única)',
  !core.deveSubstituir(pendente('quick-add'), pendente('quick-add')));
conferir('não anula o que já foi enviado',
  !core.deveSubstituir({ kind: 'status', state: 'failed' }, pendente('status')));
conferir('aguenta ausência de dados', !core.deveSubstituir(null, pendente('status')) && !core.deveSubstituir(pendente('status'), null));

// ------------------------------------------------------------------
// Sessão expirada x erro de conteúdo
// ------------------------------------------------------------------
console.log('\nsessão expirada x erro de conteúdo');
const ok = (extra = {}) => ({ status: 200, redirected: false, url: 'http://x/tasks/1/status', ...extra });

conferir('200 não é problema de sessão', !core.pareceProblemaDeSessao(ok(), null));
conferir('401 é sessão', core.pareceProblemaDeSessao(ok({ status: 401 }), null));
conferir('403 é sessão', core.pareceProblemaDeSessao(ok({ status: 403 }), null));
conferir('redirecionado para o login é sessão',
  core.pareceProblemaDeSessao(ok({ redirected: true, url: 'http://x/auth/login?next=%2F' }), null));
conferir('erro de validação não é sessão',
  !core.pareceProblemaDeSessao(ok({ status: 400 }), { errors: { title: ['Informe um título.'] } }));
conferir('mensagem do CSRF é reconhecida como sessão',
  core.pareceProblemaDeSessao(ok({ status: 400 }),
    { error: 'Sua sessão expirou ou o formulário ficou aberto por muito tempo.' }));
conferir('"sessão" no texto basta', core.pareceProblemaDeSessao(ok({ status: 400 }), { error: 'Sessão inválida.' }));
conferir('404 não é sessão', !core.pareceProblemaDeSessao(ok({ status: 404 }), { error: 'Tarefa não encontrada.' }));
conferir('aguenta resposta ausente', !core.pareceProblemaDeSessao(null, null));

// ------------------------------------------------------------------
// Mensagem de erro para o usuário
// ------------------------------------------------------------------
console.log('\nmensagem de erro para o usuário');
conferir('usa o campo error', core.mensagemDeErro({ error: 'Recusado.' }) === 'Recusado.');
conferir('junta os erros de validação',
  core.mensagemDeErro({ errors: { title: ['Título obrigatório.'], date: ['Data inválida.'] } })
    === 'Título obrigatório. Data inválida.');
conferir('sem erro reconhecível devolve vazio', core.mensagemDeErro({}) === '');
conferir('aguenta nulo', core.mensagemDeErro(null) === '');

// ------------------------------------------------------------------
// Rascunho
// ------------------------------------------------------------------
console.log('\nrascunho guardado');
const baseline = { title: '', notes: '' };
const digitado = { title: 'Comprar pão', notes: '' };

conferir('sem rascunho, nada a fazer', core.decisaoDeRascunho(null, baseline) === 'nada');
conferir('tela igual à do rascunho: restaura',
  core.decisaoDeRascunho({ values: digitado, baseline: baseline, submittedAt: null }, baseline) === 'restaurar');
conferir('servidor mudou desde o rascunho: descarta',
  core.decisaoDeRascunho({ values: digitado, baseline: baseline, submittedAt: null },
    { title: 'Outro título', notes: '' }) === 'descartar');
conferir('já enviado sem confirmação: pergunta',
  core.decisaoDeRascunho({ values: digitado, baseline: baseline, submittedAt: 123 }, baseline) === 'perguntar');
conferir('rascunho sem conteúdo é ignorado',
  core.decisaoDeRascunho({ baseline: baseline }, baseline) === 'nada');

// ------------------------------------------------------------------
// O que entra na fila
// ------------------------------------------------------------------
console.log('\no que entra na fila de envio');
const base = { marcado: null, metodo: 'post', caminho: '/tasks/quick', mesmaOrigem: true, temArquivo: false };
const com = (extra) => core.deveEnfileirar({ ...base, ...extra });

conferir('POST comum entra na fila', com({}));
conferir('marcado como "off" fica de fora', !com({ marcado: 'off' }));
conferir('marcado como "ajax" tem tratamento próprio', !com({ marcado: 'ajax' }));
conferir('GET não entra', !com({ metodo: 'get' }));
conferir('outro domínio não entra', !com({ mesmaOrigem: false }));
conferir('entrar/sair não entra', !com({ caminho: '/auth/logout' }));
conferir('backup não entra', !com({ caminho: '/backup/restore' }));
conferir('formulário com arquivo não entra', !com({ temArquivo: true }));
conferir('tarefa comum entra', com({ caminho: '/tasks/12/status' }));
conferir('adiar entra', com({ caminho: '/tasks/12/migrate' }));

// ------------------------------------------------------------------
// Assinatura de formulário
// ------------------------------------------------------------------
console.log('\nassinatura de formulário');
conferir('mesma entrada, mesma assinatura', core.assinatura('a,b,c') === core.assinatura('a,b,c'));
conferir('entradas diferentes, assinaturas diferentes', core.assinatura('a,b,c') !== core.assinatura('a,b,d'));
conferir('ordem importa', core.assinatura('a,b') !== core.assinatura('b,a'));
conferir('vazio não quebra', typeof core.assinatura('') === 'string');

console.log('\n' + (total - falhas) + '/' + total + ' verificações passaram');
if (falhas) {
  console.log(falhas + ' falha(s)');
  process.exit(1);
}
