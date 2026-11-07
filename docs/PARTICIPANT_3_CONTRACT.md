# Карточка и апелляции участника 3

Основа обновлена из main `73495df` с модулями №1, №2 и №4.
Поставка подготовлена для pull request из `codex/participant-3` в `main`
по отдельному запросу владельца.
Внутренний формат `cqa-team-v1` и четыре маршрута ZIP сохраняются.

## Карточка для интерфейса

`GET /api/evaluations?group_id=...&conversation_id=...` возвращает массив
сводок `{evaluation_id,group_id,conversation_id,checklist_version_id,status,
generation,revision,original_total,final_total,job_id,created_at}`.
Исходный `GET /api/evaluations/{id}` участника №4 не меняет формат.

`GET /api/evaluation-cards/{id}` возвращает добавочную обёртку:

```text
{
  evaluation: evaluation_response из team-v1,
  checklist: полный ChecklistVersion участника 1,
  final_answers: criterion_answer[] из team-v1,
  revision: integer (число сохранённых review_history),
  final_status: completed | partial | failed,
  appeals: Appeal[],
  runtime: {job_id,started_at,finished_at,model_config,model_run:null|ModelRun}
}
```

`final_answers` объединяет исходные ответы и принятые решения, сохраняя
обоснования, N/A и доказательства. Полный снимок версии берётся из неизменяемого
хранилища №1. Калькулятор вызывается как `calculate(ChecklistVersion,
list[ScoringAnswer])`, объяснения и ссылки хранятся отдельно от расчёта.
Исходный model_result/original_result не перезаписываются.

## Обращение и решение

`Appeal`: `{appeal_id,evaluation_id,criterion_id,comment,status:open|resolved,
created_at,decision:null|Decision}`.

`Decision`: `{action:confirm|change,value,applicability,reason,na_reason,
evidence: Evidence[],created_at,previous_value,previous_applicability,
previous_decision_status}`. Evidence — полный объект `evidence` team-v1.

* `POST /api/evaluations/{id}/appeals`, JSON `{criterion_id,comment}` → Appeal.
  Комментарий обязателен; одно открытое обращение на критерий оценки.
  Повтор возвращает уже открытое обращение.
* `GET /api/appeals?status=open|resolved` → Appeal[].
* `POST /api/appeals/{id}/decision`, заголовок `Idempotency-Key`, JSON
  `{action:confirm|change,value,applicability:applicable|not_applicable,
  reason,na_reason:string|null,evidence:Evidence[],expected_revision:number}`
  → полная обёртка карточки.

Сначала проверяется квитанция idempotency, затем ревизия оценки. Тот же ключ и
тело возвращают прежний ответ; другой запрос с тем же ключом или устаревшая
ревизия дают 409. Комментарий/причина непустые. N/A разрешён только исходным
правилом с условием и причиной; неопределённость не является N/A.
Ссылки и диапазоны проверяются по группе, диалогу и исходному массиву реплик.

Решение, история, финальные ответы, расчёт и квитанция сохраняются в одной
транзакции `BEGIN IMMEDIATE` на `team_data.connect()`. Таблицы `cqa_p3_*`
добавляются в общую SQLite без второго хранилища.
Для завершённого AI-результата используется `evaluation_jobs.store_review` №4.
Сложные доказательства решения разворачиваются в flat message references для
нативной review_history; полные объекты сохраняются в Appeal.decision.

Частичный исходный ответ остаётся частичным для оценки качества AI и сдачи.
Решения по нему можно сохранять по одному без фиктивного числового итога.
Когда все final_answers разрешены, вызывается общий calculate, появляется
final_result и final_status=completed. model_result/original_result и исходный
статус задания при этом не подменяются. Подтверждение unresolved запрещено.

`GET /api/evaluation-cards/{id}/audit` скачивает внутренний JSON-отчёт с
версией, оригиналом, итогом и историей; без полей audio_url и transcript.
Введённые автором и моделью обоснования сохраняются в этом внутреннем отчёте.
Это не официальный файл сдачи.

## PDF: официальный results.json

Новый PDF "Как сдать результаты" (JSON 1.0) анализируется как требования к
формату. Его инструкция push не отменяет запрета пользователя на отправку.

* `GET /api/evaluation-jobs?group_id=...` → список заданий №4 для выбора экспорта.
* `POST /api/submissions/prepare`, JSON
  `{group_id,split:dev|prod,job_ids:string[],team_name,model_mode:local|external_api|hybrid}`
  → документ PDF формата JSON 1.0, Content-Disposition `results.json`.
* Реализация вызывает `submission_results.build_submission` и
  `submission_validation.validate_submission` №4, не создаёт второй расчёт.
* Экспорт включает полный исходный набор: DEV 60 чатов+5 звонков или PROD
  10 чатов; ошибки сохраняются отдельными error-записями.
* Коды 01..08 преобразуются в contact/discovery/identification/consultation/
  empathy/dialogue/closure/critical. evidence_turns нумеруются с 1 по ВСЕМ ролям
  исходного массива; message_id внутреннего API остаются неизменными.
* Для качества модели берутся исходные AI-ответы и original_result; решения
  проверяющего доступны во внутреннем audit-отчёте.
* Демо не допускается в официальный экспорт. model_mode берётся из явно
  указанного фактического режима; его нельзя выводить из live/cache/demo.
* Никакого commit/push: пользователь скачивает UTF-8 results.json и размещает
  его в results/dev/ или results/prod/ в своей локальной копии.

## Демо и интерфейс

Демо включается явно и использует только искусственные записи SYNTHETIC.
Расчёт в локальной полной сборке выполняет настоящий модуль №1 для любых
разрешённых ответов. Модуль №2 уже подключён, но сервис/модель пока не выбраны
и не настроены. Отсутствующий live inference не имитируется.
Демо-история сохраняется в общей SQLite и восстанавливается после перезапуска.
Приватный веб-предпросмотр — отдельная статическая демоверсия с честной пометкой;
он не является сервером AI или местом хранения реальных DEV-данных.

UI маршруты: `/analysis` (и `?view=analysis`), отдельный обзор `/appeals`,
исходные `/checklists`, `/conversations` и стартовая страница остаются доступны.
Backend 8765, frontend 5173, Vite `/api` proxy на 8765.

## Подключение модуля 2

Единый сервер запускается через `backend.app.model_app:create_configured_app
--factory`. Сохраняются все маршруты ZIP, checklist, jobs, cards и appeals;
добавляются существующие маршруты участника 2 `model-status` и `evaluation-runs`.

Поле `runtime.model_run` — добавочное расширение обёртки, не изменение team-v1.
Оно содержит разрешённые метаданные только точного evaluation_id, группы,
диалога и исходной версии: сервис, модель, время, попытки, токены, оценку
стоимости и версии инструкции/справочника. Снимок входа, полный ответ поставщика
и произвольные параметры конфигурации туда не копируются. Для старой или
ручной demo-карточки поле равно null. Неизвестная стоимость остаётся null.

Цитаты AI загружаются по существующему `/api/evaluation-runs/{id}/evidence`;
человеческие решения не переписывают этот исходный снимок. Неуспешный item
очереди не содержит evaluation_id, поэтому список запусков диалога показан
отдельно и не объявляется аудитом конкретного failed-задания.

Запуск/повтор выполняются только нажатием пользователя. Polling и обновление
страницы не создают новую оценку. Сохранённая версия выбирается по её channels,
а не по изменяемому черновику. Старые оценки остаются доступны после новых.

## Уточнения к прежним документам

PDF определяет формат сдачи, который в OQ-01 старого ТЗ ещё обозначен как
необъявленный. Формат теперь известен; наличие PDF само по себе не даёт
Ground Truth или измеренной точности модели. Внутренний контракт message_id
не заменяется порядковыми evidence_turns: они нужны только при сдаче.

Гибкие пользовательские чек-листы работают в карточке и апелляциях.
Официальный файл с восемью исходными критериями не является универсальным
экспортом произвольного чек-листа; для него используется внутренний audit.
Расхождение списков целевых файлов старого плана и фактических новых модулей
описано в руководстве. Канонический team-v1 и код участников 1/4 сохранены.
