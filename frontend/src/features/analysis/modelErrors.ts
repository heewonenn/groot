const messages: Record<string, string> = {
  static_preview: 'Статический предпросмотр не вызывает модель. Для настоящих оценок запустите полную сборку FastAPI.',
  model_not_ready: 'Модель ещё не готова к запуску. Проверьте состояние и серверные настройки.',
  model_disabled: 'Модель отключена в настройках сервера. Команде нужно выбрать и настроить модель.',
  model_configuration_missing: 'Не заполнены серверные настройки модели. Нужны модель, ключ и подтверждённый размер контекста.',
  model_configuration_invalid: 'Серверные настройки модели некорректны. Проверьте конфигурацию перед запуском.',
  provider_not_supported: 'Для выбранного сервиса ещё не подключён адаптер.',
  settings_not_supported: 'Настройки задания не совпадают с конфигурацией модели на сервере.',
  input_invalid: 'Входной диалог или версия чек-листа не прошли проверку.',
  input_too_large: 'Полный вход превышает предел модели. Транскрипт не обрезался.',
  context_budget_exceeded: 'Вход с правилами и резервом ответа не помещается в подтверждённый контекст модели.',
  answer_json_invalid: 'Модель вернула некорректный JSON.',
  answer_schema_invalid: 'Ответ модели не соответствует общему формату.',
  answer_rules_invalid: 'Ответ модели содержит недопустимое значение или нарушает правило чек-листа.',
  answer_scope_invalid: 'Модель заявила проверку материалов, которые не были ей переданы.',
  answer_knowledge_invalid: 'Модель сослалась на неподтверждённый источник справочника.',
  answer_na_invalid: 'N/A в ответе модели не соответствует правилу критерия.',
  answer_na_evidence_missing: 'Для N/A не хватает подтверждающего контекста.',
  answer_absence_span_missing: 'Для отсутствующего действия модель не указала проверенный диапазон.',
  answer_anchor_outside_span: 'Опорная реплика модели находится вне указанного диапазона.',
  answer_review_invalid: 'Модель не указала основание для ручной проверки.',
  answer_unresolved_invalid: 'Неразрешённый ответ модели оформлен некорректно.',
  provider_auth: 'Сервис отклонил серверный ключ или доступ к модели.',
  provider_refused: 'Модель отказалась обрабатывать запрос.',
  provider_incomplete: 'Сервис вернул незавершённый ответ.',
  provider_network: 'Не удалось связаться с сервисом модели.',
  provider_transient: 'Сервис модели временно недоступен или ограничил частоту запросов.',
  provider_rate_limit: 'Достигнут лимит запросов модели. Проверьте квоту сервиса и повторите оценку позже.',
  provider_timeout: 'Сервис модели не ответил за отведённое время.',
  provider_rejected: 'Сервис отклонил запрос. Проверьте совместимость выбранной модели.',
  provider_no_private_route: 'OpenRouter не нашёл провайдера с выбранными условиями использования данных.',
  provider_no_route: 'OpenRouter не нашёл провайдера для модели с такими параметрами.',
  provider_bad_request: 'Google отклонил запрос на оценку (HTTP 400).',
  provider_region_unavailable: 'Google сообщает, что генерация недоступна из региона запроса.',
  provider_precondition: 'Google отклонил генерацию из-за условий доступа проекта (FAILED_PRECONDITION).',
  provider_generation_config: 'Google отклонил настройки генерации или схему ответа.',
  provider_model_unavailable: 'Google сообщает, что эта модель недоступна для генерации.',
  provider_response_too_large: 'Ответ сервиса превысил допустимый размер.',
  provider_envelope_invalid: 'Сервис вернул ответ неподдерживаемого формата.',
  evaluation_deadline: 'Бюджет времени оценки исчерпан. Поздний ответ не принят.',
  attempts_exhausted: 'Допустимые попытки оценки исчерпаны.',
  evaluation_id_already_used: 'Такой идентификатор запуска уже сохранён.',
  evaluation_timeout: 'Оценка не завершилась вовремя.',
  evaluation_failed: 'Диалог не удалось оценить. Подробности доступны в истории запусков модели.',
  server_restart: 'Сервер перезапустился во время оценки. Автоматический повтор не выполнялся.',
  internal_error: 'Сервер не смог обработать оценку. Результат не заменялся демонстрационным.',
}

export function modelErrorText(code: string | null | undefined) {
  if (!code) return 'Причина не указана сервером.'
  if (messages[code]) return messages[code]
  if (code.startsWith('answer_')) return 'Ответ модели не прошёл проверку. Нужен явный повтор после проверки причины.'
  return /^[a-z0-9_]{1,100}$/.test(code) ? `Ошибка оценки: ${code}.` : 'Оценка не выполнена.'
}

export function modelRunStatus(status: string) {
  const statuses: Record<string, string> = {
    queued: 'Ожидает запуска', running: 'Выполняется', completed: 'Завершён',
    partial: 'Частичный ответ', failed: 'Ошибка', interrupted: 'Прерван',
  }
  return statuses[status] ?? 'Состояние не определено'
}
