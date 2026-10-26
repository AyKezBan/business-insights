/* Explicit Java /api/v1 DTO adapter. Authentication belongs to the same-origin proxy. */
'use strict';
(function (root) {
  const roles = {owner:'Собственник', ceo:'Генеральный директор', cfo:'Финансовый директор', chief_accountant:'Главный бухгалтер', treasurer:'Казначей', other_c_level:'Другой руководитель', other:'Другая роль'};
  const views = {overview:'Общий обзор', consequences:'Последствия', risks:'Риски', recommendations:'Рекомендации'};
  const list = value => Array.isArray(value) ? value : [];
  function outcome(status, value) {
    if (status === 'FAILED') return 'FAILED';
    const outcomes = {full:'COMPLETE', limited:'LIMITED', refused:'REFUSED', none:'NO_RESULT'};
    if (value != null) {
      if (!Object.hasOwn(outcomes,value)) throw new Error('Сервис вернул неизвестный исход анализа.');
      return outcomes[value];
    }
    return status === 'SUCCEEDED' ? 'COMPLETE' : status;
  }
  const preferenceKey = (ctx, id) => `${ctx.company_id}:${ctx.role}:${id}`;
  const pathId = id => encodeURIComponent(id);
  function checkContext(dto, ctx, id) {
    if (!dto || dto.companyId !== ctx.company_id || dto.role !== ctx.role || (id && dto.insightId !== id)) throw new Error('Ответ API содержит другой контекст компании или карточки.');
  }
  function savedFeedback(value) {
    if (!value || !['USEFUL','IRRELEVANT','UNSUPPORTED','OTHER'].includes(value.rating)) return null;
    if (value.rating === 'USEFUL') return {feedback:'useful',reason:null};
    const reason = value.rating === 'IRRELEVANT' ? 'company'
      : value.rating === 'UNSUPPORTED' ? (value.comment === 'Есть ошибка' ? 'error' : 'basis')
      : value.comment === 'Не помогает принять решение' ? 'decision' : value.comment === 'Другое' ? 'other' : null;
    return {feedback:'not_useful',reason};
  }
  function source(dto) {
    return {id:dto.sourceId, title:dto.title, url:dto.url, date:dto.date, page:dto.page, kind:dto.kind,
      doc_id:dto.docId, content_sha256:dto.contentSha256, excerpt:dto.excerpt, publisher_id:dto.publisherId,
      adapter_id:dto.adapterId, material_type:dto.materialType, completeness:dto.completeness, language:dto.language,
      published_date:dto.publishedDate, published_precision:dto.publishedPrecision, raw_publication:dto.rawPublication,
      source_timezone:dto.sourceTimezone, source_utc_offset_seconds:dto.sourceUtcOffsetSeconds, timezone_provenance:dto.timezoneProvenance, first_seen_at:dto.firstSeenAt,
      retrieved_at:dto.retrievedAt, statement_type:dto.statementType, target_period:dto.targetPeriod,
      valid_until:dto.validUntil, rights_url:dto.rightsUrl, translation_attribution:dto.translationAttribution};
  }
  function part(claim, kind, label) {
    const spans=claim.evidenceSpans===undefined ? [] : claim.evidenceSpans;
    if (!Array.isArray(spans)) throw new Error('Сервис вернул некорректные фрагменты оснований.');
    const evidence_spans=spans.map(span=>{
      if (!span || typeof span!=='object' || Array.isArray(span) || Object.keys(span).length!==4
          || !['sourceId','contentSha256','start','end'].every(key=>Object.hasOwn(span,key))
          || typeof span.sourceId!=='string' || !span.sourceId || typeof span.contentSha256!=='string' || !/^[a-f0-9]{64}$/.test(span.contentSha256)
          || !Number.isSafeInteger(span.start) || !Number.isSafeInteger(span.end) || span.start<0 || span.end<=span.start)
        throw new Error('Сервис вернул некорректный фрагмент основания.');
      // Offsets are Unicode code points in the exact source body; the display text is already rendered by ML.
      return {source_id:span.sourceId,content_sha256:span.contentSha256,start:span.start,end:span.end};
    });
    return {kind, claim_id:claim.claimId, text:claim.text, certainty:claim.kind, source_ids:list(claim.sourceIds), feature_refs:list(claim.profileFeatures), evidence_spans, origin:claim.origin, level:claim.level, horizon:claim.horizon, ...(label ? {label} : {})};
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
    return {...(Object.hasOwn(dto,'feedback') ? {feedback:savedFeedback(dto.feedback)} : {}),context:{...ctx},insight_id:dto.insightId,event_id:dto.eventId,title:dto.headline,status:dto.mlStatus,
      outcome:outcome(dto.status,dto.outcome),ml_outcome:dto.outcome,operation_status:dto.status,error_code:dto.errorCode,
      ml_insight_id:dto.mlInsightId,run_id:dto.runId,version:dto.version,opened_at:dto.openedAt,request_id:dto.requestId,parent_run_id:dto.parentRunId,
      cache_hit:dto.cacheHit,as_of:dto.asOf,corpus_revision:dto.corpusRevision,event_content_sha256:dto.eventContentSha256,
      conflicts:list(dto.conflicts).map(c=>({reason:c.reason,source_ids:list(c.sourceIds),resolved:c.resolved})),
      outcome_message:list(dto.warnings).join(' '),blocks,sources:dto.sources.map(source),
      missing_data:[...dto.missingData,...list(dto.warnings)],disclaimer:dto.disclaimer || '',generated_at:dto.generatedAt};
  }
  function createBindings() {
    const cards = new Map(), feedback = new Map();
    function remember(dto,ctx) {
      cards.set(dto.insightId,dto);
      if (Object.hasOwn(dto,'feedback')) {
        const key=preferenceKey(ctx,dto.insightId)+':'+dto.insightId, saved=savedFeedback(dto.feedback);
        if (saved) feedback.set(key,saved); else feedback.delete(key);
      }
    }
    async function loadFeed(ctx, options) {
      const items=[];
      for (let offset=0;;offset+=100) {
        const query = new URLSearchParams({role:ctx.role,includeHidden:'true',limit:'100',offset:String(offset)});
        const result=await options.transport(`/api/v1/companies/${pathId(ctx.company_id)}/insights?${query}`,options);
        if (!Array.isArray(result?.items)) throw new Error('Сервис вернул некорректную ленту.');
        for (const dto of result.items) { checkContext(dto,ctx); remember(dto,ctx); items.push(dto); }
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
        return {person_id:session.personId,profile:{name:session.personId,company_id:companies[0].companyId,role:'cfo'},
          companies:companies.map(c=>({id:c.companyId,name:'Компания '+c.companyId,sector:c.okvedName || 'Не указана',
            ...(Array.isArray(c.roles) ? {roles:c.roles.filter(role=>Object.hasOwn(roles,role))} : {})})),roles,views,
          notice:'Доступны компании текущего пользователя. Аналитическая роль задаёт контекст анализа. В ленте показаны сохранённые результаты; новый анализ можно запустить из каталога материалов.'};
      },
      async materialCatalog(ctx, options) {
        const items=[], seen=new Set(); let token=null, total=null, offset=0;
        while (true) {
          const query=new URLSearchParams({limit:'100',offset:String(offset)});
          if (token) query.set('revision',token.revision);
          const page=await options.transport(`/api/v1/materials/events?${query}`,{...options,method:'GET'});
          const current=page?.catalogToken;
          if (!current || typeof current.storeId !== 'string' || !current.storeId || !Number.isSafeInteger(current.generation) || current.generation<0
              || !/^[a-f0-9]{64}$/.test(current.revision || '') || !Array.isArray(page.items)
              || page.offset!==offset || !Number.isSafeInteger(page.limit) || page.limit<1 || page.limit>100
              || page.items.length>page.limit || !Number.isSafeInteger(page.total) || page.total<0) throw new Error('Сервис вернул некорректную страницу каталога.');
          if (token && (current.storeId!==token.storeId || current.generation!==token.generation || current.revision!==token.revision || page.total!==total)) throw new Error('Версия каталога изменилась во время загрузки. Обновите список материалов.');
          if (!token) { token={...current}; total=page.total; }
          for (const dto of page.items) {
            if (typeof dto.eventId!=='string' || !dto.eventId || seen.has(dto.eventId)) throw new Error('Каталог содержит повторяющийся или некорректный материал.');
            if (dto.corpusRevision!==token.revision || !/^[a-f0-9]{64}$/.test(dto.eventContentSha256 || '')
                || typeof dto.title!=='string' || !dto.title || typeof dto.text!=='string'
                || !dto.source?.sourceId || dto.source.kind!=='news') throw new Error('Материал не соответствует выбранной версии каталога.');
            seen.add(dto.eventId);
            items.push({event_id:dto.eventId,title:dto.title,text:dto.text,published_at:dto.publishedAt,published_date:dto.publishedDate,
              published_precision:dto.publishedPrecision,source:source(dto.source),related_sources:list(dto.relatedSources).map(source),
              topics:list(dto.topics),conflicts:list(dto.conflicts).map(c=>({reason:c.reason,source_ids:list(c.sourceIds),resolved:c.resolved})),
              event_content_sha256:dto.eventContentSha256,corpus_revision:dto.corpusRevision});
          }
          offset+=page.items.length;
          if (offset>total || (offset<total && page.items.length<page.limit)) throw new Error('Каталог содержит неполную или противоречивую страницу материалов.');
          if (offset===total) return {context:{company_id:ctx.company_id,role:ctx.role},catalog_token:{store_id:token.storeId,generation:token.generation,revision:token.revision},items,total};
        }
      },
      async analyze(input, options) {
        const frozen=Object.freeze({companyId:input.company_id,role:input.role,eventId:input.event_id,corpusRevision:input.corpus_revision,
          eventContentSha256:input.event_content_sha256,requestId:input.request_id,userProvided:Object.freeze({...input.user_provided})});
        if (!frozen.companyId || !Object.hasOwn(roles,frozen.role) || !frozen.eventId || !frozen.requestId
            || !/^[a-f0-9]{64}$/.test(frozen.corpusRevision || '') || !/^[a-f0-9]{64}$/.test(frozen.eventContentSha256 || '')) throw new Error('Для анализа не выбран полный контекст материала.');
        let operationURL;
        const onProgress=progress=>{ operationURL=progress.location; options.onProgress?.(progress); };
        const preserveResume=error=>{
          if (!error.resume && operationURL && ![401,403].includes(error.status)) error.resume=async signal=>{
            try { return await options.transport(operationURL,{...options,signal,onProgress,method:'GET',body:undefined,requestId:undefined}); }
            catch (next) { preserveResume(next); throw next; }
          };
          return error;
        };
        return root.InsightsIntegration.mapOperation(async()=>{
          try { return await options.transport('/api/v1/analyses',{...options,onProgress,method:'POST',body:frozen,requestId:frozen.requestId}); }
          catch (error) { throw preserveResume(error); }
        },async(operation,signal)=>{
          const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
          if (operation?.kind!=='ANALYSIS' || !uuid.test(operation.operationId || '')) throw new Error('Сервис вернул другую или некорректную операцию.');
          if (operation.requestId!=null && operation.requestId!==frozen.requestId) throw new Error('Операция относится к другому запросу анализа.');
          if (operation.corpusRevision!=null && operation.corpusRevision!==frozen.corpusRevision) throw new Error('Операция использует другую версию материалов.');
          if (['NO_RESULT','INSUFFICIENT_DATA','FAILED'].includes(operation.status)) {
            return {context:{company_id:frozen.companyId,role:frozen.role},insight_id:null,operation_id:operation.operationId,event_id:frozen.eventId,
              outcome:outcome(operation.status,operation.outcome),ml_outcome:operation.outcome,error_code:operation.error?.code,
              operation_request_id:operation.requestId,corpus_revision:frozen.corpusRevision,event_content_sha256:frozen.eventContentSha256,
              outcome_message:operation.error?.message || list(operation.warnings).join(' '),missing_data:list(operation.warnings)};
          }
          if (!['SUCCEEDED','LIMITED'].includes(operation.status)) throw new Error('Анализ не завершён: '+operation.status);
          const id=operation.insightId || operation.operationId;
          if (!uuid.test(id || '')) throw new Error('Сервис не передал корректный идентификатор карточки.');
          let dto;
          try { dto=await options.transport(`/api/v1/insights/${pathId(id)}`,{...options,signal,method:'GET'}); }
          catch (error) { if (![401,403].includes(error.status)) error.resume=async()=>operation; throw error; }
          const ctx={company_id:frozen.companyId,role:frozen.role,event_id:id,view:'overview'};
          checkContext(dto,ctx,id);
          if (dto.eventId!==frozen.eventId || dto.corpusRevision!==frozen.corpusRevision || dto.eventContentSha256!==frozen.eventContentSha256) throw new Error('Результат анализа не соответствует выбранной версии материала.');
          const result=cardView(dto,ctx); remember(dto,ctx); return result;
        },options.signal);
      },
      async feed(ctx, options) {
        const items=await loadFeed(ctx,options);
        return {context:{company_id:ctx.company_id,role:ctx.role},items:items.map(dto=>({id:dto.insightId,insight_id:dto.insightId,
          title:dto.headline,category:dto.impactLevel ? 'Влияние: '+dto.impactLevel : 'Анализ события',date:(dto.generatedAt || '').slice(0,10),
          why:list(dto.whyItMatters).map(c=>c.text).join(' '),status:dto.mlStatus,outcome:outcome(dto.status,dto.outcome),ml_outcome:dto.outcome,missing_data:[...list(dto.missingData),...list(dto.warnings)]})),
          as_of:items.map(i=>(i.generatedAt || '').slice(0,10)).sort().at(-1) || 'нет результатов',empty_reason:'Для этой компании и роли пока нет сохранённых результатов анализа.'};
      },
      async insight(ctx, options) {
        const dto=await options.transport(`/api/v1/insights/${pathId(ctx.event_id)}`,options);
        const result=cardView(dto,ctx); remember(dto,ctx); return result;
      },
      async preferences(ctx, options) {
        const items=await loadFeed(ctx,options), result={};
        for(const dto of items) { const key=preferenceKey(ctx,dto.insightId); result[key]={hidden:dto.hidden===true};
          if(feedback.has(key+':'+dto.insightId)) result[key+':'+dto.insightId]=feedback.get(key+':'+dto.insightId); }
        // Each returned feedback field refreshes the cache, including explicit nulls; older DTOs may omit it.
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
        feedback.set(preferenceKey(input.context,id)+':'+id,savedFeedback({rating,comment}));
        if (dto) dto.feedback={rating,comment};
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
            if (answer.companyId != null && answer.companyId !== input.company_id) throw new Error('Ответ содержит другой контекст компании.');
            if (dto && ((answer.eventId != null && answer.eventId !== dto.eventId)
                || (answer.mlInsightId != null && dto.mlInsightId != null && answer.mlInsightId !== dto.mlInsightId))) throw new Error('Ответ относится к другой карточке.');
            if (dto?.corpusRevision != null && [operation.corpusRevision,answer.corpusRevision].some(revision=>revision != null && revision !== dto.corpusRevision)) throw new Error('Ответ использует другую версию материалов.');
            const mlOutcome=operation.outcome ?? answer.outcome;
            return {context:ctx,insight_id:input.insight_id,request_id:input.request_id,answer_id:answer.operationId,
              operation_request_id:operation.requestId,ml_insight_id:answer.mlInsightId,run_id:answer.runId,
              company_id:answer.companyId,event_id:answer.eventId,as_of:answer.asOf,
              corpus_revision:answer.corpusRevision ?? operation.corpusRevision,event_content_sha256:answer.eventContentSha256,
              status:answer.mlStatus,outcome:outcome(answer.status,mlOutcome),ml_outcome:mlOutcome,operation_status:operation.status,
              error_code:operation.error?.code,text:answer.text,sources:list(answer.sources).map(source),
              missing_data:[...list(answer.missingData),...list(answer.warnings)],claims:list(answer.claims).map(c=>part(c,'answer')),disclaimer:answer.disclaimer};
          },options.signal);
      }
    };
  }
  root.InsightsBackend={createBindings};
})(typeof globalThis==='object' ? globalThis : this);
