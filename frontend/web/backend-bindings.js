/* Explicit Java /api/v1 DTO adapter. Authentication belongs to the same-origin proxy. */
'use strict';
(function (root) {
  const roles = {owner:'Собственник', ceo:'Генеральный директор', cfo:'Финансовый директор', chief_accountant:'Главный бухгалтер', treasurer:'Казначей', other_c_level:'Другой руководитель', other:'Другая роль'};
  const views = {overview:'Общий обзор', consequences:'Последствия', risks:'Риски', recommendations:'Рекомендации'};
  const list = value => Array.isArray(value) ? value : [];
  const outcome = status => status === 'SUCCEEDED' ? 'COMPLETE' : status;
  const preferenceKey = (ctx, id) => `${ctx.company_id}:${ctx.role}:${id}`;
  const pathId = id => encodeURIComponent(id);
  function checkContext(dto, ctx, id) {
    if (!dto || dto.companyId !== ctx.company_id || dto.role !== ctx.role || (id && dto.insightId !== id)) throw new Error('Ответ API содержит другой контекст компании или карточки.');
  }
  function source(dto) {
    return {id:dto.sourceId, title:dto.title, url:dto.url, date:dto.date, page:dto.page, kind:dto.kind};
  }
  function part(claim, kind, label) {
    return {kind, text:claim.text, certainty:claim.kind, source_ids:list(claim.sourceIds), feature_refs:list(claim.profileFeatures), origin:claim.origin, ...(label ? {label} : {})};
  }
  function cardView(dto, ctx) {
    checkContext(dto, ctx, ctx.event_id);
    if (!dto.insightId || !dto.headline || !Array.isArray(dto.sources) || !Array.isArray(dto.missingData)) throw new Error('Сервис вернул неполную карточку.');
    const blocks = [{kind:'fact', claims:list(dto.whatHappened).map(c=>part(c,'fact'))}, ...['open','bank','ai'].map(kind=>({kind, sections:[]}))];
    const add = (claims, kind, label) => {
      for (const claim of list(claims)) {
        const origin = claim.origin === 'BANK_ANALYSIS' ? 'bank' : claim.origin === 'EXTERNAL_ASSESSMENT' ? 'open' : 'ai';
        blocks.find(b=>b.kind===origin).sections.push(part(claim,kind,label));
      }
    };
    add(dto.whyItMatters,'consequences','Почему это важно');
    add(dto.consequences,'consequences'); add(dto.risks,'risks');
    add(dto.opportunities,'consequences','Возможности'); add(dto.actions,'recommendations');
    // bankView is a separate, unthemed assessment; do not invent bank risks/actions.
    add(dto.bankView,'consequences','Оценка из аналитического материала');
    for (const scenario of list(dto.scenarios)) blocks.find(b=>b.kind==='ai').sections.push({kind:'consequences', label:'Сценарий: '+scenario.name,
      text:[scenario.trigger,scenario.implication,scenario.plausibility].filter(Boolean).join(' · '),certainty:'hypothesis',source_ids:list(scenario.sourceIds)});
    for (const text of list(dto.modelVsBank)) blocks.find(b=>b.kind==='ai').sections.push({kind:'consequences',label:'Сопоставление модели и банка',text,certainty:'inference',source_ids:[]});
    return {context:{...ctx},insight_id:dto.insightId,event_id:dto.eventId,title:dto.headline,status:dto.mlStatus,
      outcome:outcome(dto.status),outcome_message:list(dto.warnings).join(' '),blocks,sources:dto.sources.map(source),
      missing_data:[...dto.missingData,...list(dto.warnings)],disclaimer:dto.disclaimer || '',generated_at:dto.generatedAt};
  }
  function createBindings() {
    const cards = new Map(), feedback = new Map();
    async function loadFeed(ctx, options) {
      const items=[];
      for (let offset=0;;offset+=100) {
        const query = new URLSearchParams({role:ctx.role,includeHidden:'true',limit:'100',offset:String(offset)});
        const result=await options.transport(`/api/v1/companies/${pathId(ctx.company_id)}/insights?${query}`,options);
        if (!Array.isArray(result?.items)) throw new Error('Сервис вернул некорректную ленту.');
        for (const dto of result.items) { checkContext(dto,ctx); cards.set(dto.insightId,dto); items.push(dto); }
        if (result.items.length<100) return items;
      }
    }
    return {
      async bootstrap(_input, options) {
        const session=await options.transport('/api/v1/session',options);
        const companies=[];
        for (let offset=0;;offset+=100) {
          const page=await options.transport(`/api/v1/companies?limit=100&offset=${offset}`,options);
          if (!Array.isArray(page)) throw new Error('Сервис вернул некорректный список компаний.');
          companies.push(...page); if(page.length<100) break;
        }
        if (!session?.personId || !companies.length) throw new Error('В текущей сессии нет доступных компаний.');
        return {profile:{name:session.personId,company_id:companies[0].companyId,role:'cfo'},
          companies:companies.map(c=>({id:c.companyId,name:'Компания '+c.companyId,sector:c.okvedName || 'Не указана'})),roles,views,
          notice:'Демонстрационный доступ по связям ФЛ–ЮЛ. Роль выбирается явно и не подтверждает полномочия. Показаны сохранённые результаты анализа; открытие ленты не запускает генерацию.'};
      },
      async feed(ctx, options) {
        const items=await loadFeed(ctx,options);
        return {context:{company_id:ctx.company_id,role:ctx.role},items:items.map(dto=>({id:dto.insightId,insight_id:dto.insightId,
          title:dto.headline,category:dto.impactLevel ? 'Влияние: '+dto.impactLevel : 'Анализ события',date:(dto.generatedAt || '').slice(0,10),
          why:list(dto.whyItMatters).map(c=>c.text).join(' '),status:dto.mlStatus})),
          as_of:items.map(i=>(i.generatedAt || '').slice(0,10)).sort().at(-1) || 'нет результатов',empty_reason:'Для этой компании и роли пока нет сохранённых результатов анализа.'};
      },
      async insight(ctx, options) {
        const dto=await options.transport(`/api/v1/insights/${pathId(ctx.event_id)}`,options);
        const result=cardView(dto,ctx); cards.set(dto.insightId,dto); return result;
      },
      async preferences(ctx, options) {
        const items=await loadFeed(ctx,options), result={};
        for(const dto of items) { const key=preferenceKey(ctx,dto.insightId); result[key]={hidden:dto.hidden===true};
          if(feedback.has(key+':'+dto.insightId)) result[key+':'+dto.insightId]=feedback.get(key+':'+dto.insightId); }
        // Java currently has no feedback read endpoint: remembered ratings last for this session only.
        return result;
      },
      async savePreference(input, options) {
        const id=input.insight_id || input.context.event_id;
        if (!id) throw new Error('Не указана карточка для сохранения.');
        const dto=cards.get(id); if(dto) checkContext(dto,input.context,id);
        if(typeof input.value.hidden==='boolean') {
          await options.transport(`/api/v1/insights/${pathId(id)}/state`,{...options,method:'PUT',body:{hidden:input.value.hidden}});
          if(dto) dto.hidden=input.value.hidden;
          return;
        }
        const value=input.value;
        if(!['useful','not_useful'].includes(value.feedback)) throw new Error('Неизвестная оценка.');
        const rating=value.feedback==='useful' ? 'USEFUL' : {company:'IRRELEVANT',basis:'UNSUPPORTED',error:'UNSUPPORTED'}[value.reason] || 'OTHER';
        const comment={company:'Не относится к компании',basis:'Неясно основание',error:'Есть ошибка',decision:'Не помогает принять решение',other:'Другое'}[value.reason] || null;
        await options.transport(`/api/v1/insights/${pathId(id)}/feedback`,{...options,method:'PUT',body:{rating,comment}});
        feedback.set(preferenceKey(input.context,id)+':'+id,{feedback:value.feedback,reason:value.reason || null});
      },
      async opened(input, options) {
        return options.transport(`/api/v1/insights/${pathId(input.insight_id)}/opened`,{...options,method:'POST'});
      },
      async clarify(input, options) {
        const ctx={company_id:input.company_id,role:input.role,event_id:input.event_id,view:input.view};
        const dto=cards.get(input.insight_id); if(dto) checkContext(dto,ctx,input.insight_id);
        return root.InsightsIntegration.mapOperation(
          ()=>options.transport(`/api/v1/insights/${pathId(input.insight_id)}/questions`,{...options,method:'POST',body:{question:input.question},requestId:input.request_id}),
          async (operation,signal)=>{
            if(operation.parentInsightId!==input.insight_id) throw new Error('Уточнение относится к другой карточке.');
            if(!['SUCCEEDED','LIMITED','NO_RESULT'].includes(operation.status)) throw new Error(operation.error?.message || 'Уточнение не завершено: '+operation.status);
            const answer=await options.transport(`/api/v1/answers/${pathId(operation.operationId)}`,{...options,signal,method:'GET'});
            if(answer.insightId!==input.insight_id || answer.operationId!==operation.operationId) throw new Error('Ответ относится к другой карточке.');
            return {context:ctx,insight_id:input.insight_id,request_id:input.request_id,answer_id:answer.operationId,
              status:answer.mlStatus,outcome:outcome(answer.status),text:answer.text,sources:list(answer.sources).map(source),
              missing_data:[...list(answer.missingData),...list(answer.warnings)],claims:list(answer.claims).map(c=>part(c,'answer')),disclaimer:answer.disclaimer};
          },options.signal);
      }
    };
  }
  root.InsightsBackend={createBindings};
})(typeof globalThis==='object' ? globalThis : this);
