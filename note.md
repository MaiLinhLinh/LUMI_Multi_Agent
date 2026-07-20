(LumiMultiAgent) PS D:\RAG_ManageAgent_Lumi\code> python web_app.py
INFO:     Started server process [16412]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8501 (Press CTRL+C to quit)
INFO:     127.0.0.1:65491 - "GET / HTTP/1.1" 200 OK
INFO:     127.0.0.1:65491 - "GET /assets/app.css HTTP/1.1" 304 Not Modified
INFO:     127.0.0.1:62506 - "GET /assets/app.js HTTP/1.1" 304 Not Modified
INFO:     127.0.0.1:62506 - "GET /api/session/15a00890-a1ae-460d-9a76-24c8acc9d0fe HTTP/1.1" 200 OK
[Gemini][call=1] START_STRUCTURED model=gemma-4-26b-a4b-it input_chars=67 temperature=0.0
[Gemini][call=1] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native_structured][call=1] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=1] STRUCTURED_RESULT {'topics': ['weather'], 'execution_mode': 'single', 'primary_intent': 'weather', 'dependencies': [], 'news_query': '', 'wiki_topic': ''}
[Gemini][call=1] START_STRUCTURED model=gemma-4-26b-a4b-it input_chars=134 temperature=0.0
[Gemini][call=1] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native_structured][call=1] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=1] STRUCTURED_RESULT {'location_text': 'hà nội', 'date_text': 'ngày mai', 'time_of_day_text': None, 'normalized_time': None, 'request_type_candidate': 'forecast'}
[WEATHER_PIPELINE] {"code": "llm1_result", "result": {"date_text": "ngày mai", "location_text": "hà nội", "normalized_time": null, "request_type_candidate": "forecast", "time_of_day_text": null}, "stage": "llm1_extraction", "status": "received"}
[WEATHER_PIPELINE] {"code": "ready_for_redis", "message": {}, "stage": "validation", "status": "ready_for_redis"}
[WEATHER_REDIS] tool=get_weather_forecast location_id='ha_noi' ok=True snapshot_id='20260720T015908815675Z' lookup_ms=57.15
[WEATHER_PIPELINE] {"code": "completed", "message": "", "stage": "redis", "status": "completed"}

[WEB][WORKFLOW_METRICS]
  - Topics: ['weather']
  - Timings: {'manager': 6.907629400026053, 'weather': 2.2516731999930926}
  - LLM usage [manager]:
    model: gemma-4-26b-a4b-it
    prompt_tokens: 486
    completion_tokens: 38
    thoughts_tokens: unknown
    total_tokens: 524
    time_to_first_token: 1.095572s
    time_to_first_visible: 1.095572s
    time_to_last_visible: 1.991838s
    visible_generation_duration: 0.896266s
    total_request_time: 1.994219s
    cached_tokens: unknown
    prefix_cache_hit: no
    cache_hit_ratio: unknown
    kv_cache_hit: not_exposed_by_gemini_api
    raw_usage_keys: ['cache_tokens_details', 'cached_content_token_count', 'candidates_token_count', 'candidates_tokens_details', 'prompt_token_count', 'prompt_tokens_details', 'thoughts_token_count', 'tool_use_prompt_token_count', 'tool_use_prompt_tokens_details', 'total_token_count', 'traffic_type']
  - LLM usage [weather]:
    call_1:
      model: gemma-4-26b-a4b-it
      prompt_tokens: 829
      completion_tokens: 47
      thoughts_tokens: unknown
      total_tokens: 876
      time_to_first_token: 1.045486s
      time_to_first_visible: 1.045486s
      time_to_last_visible: 1.820549s
      visible_generation_duration: 0.775063s
      total_request_time: 1.852175s
      cached_tokens: unknown
      prefix_cache_hit: no
      cache_hit_ratio: unknown
      kv_cache_hit: not_exposed_by_gemini_api
      raw_usage_keys: ['cache_tokens_details', 'cached_content_token_count', 'candidates_token_count', 'candidates_tokens_details', 'prompt_token_count', 'prompt_tokens_details', 'thoughts_token_count', 'tool_use_prompt_token_count', 'tool_use_prompt_tokens_details', 'total_token_count', 'traffic_type']
INFO:     127.0.0.1:62506 - "POST /api/chat HTTP/1.1" 200 OK
[SemanticRouter] START prompt_chars=6432
[Gemini][call=1] START model=gemma-4-26b-a4b-it prompt_chars=9020 temperature=0.0
[Gemini][call=1] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native][call=1] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=1] RESULT {
  "status": "ready",
  "route": "domain",
  "domain_request": "thế cả tuần thì sao",
  "template": {
    "action": null,
    "source": "none",
    "template_id": null,
    "selection_index": null,
    "requirements": {},
    "extracted_keywords": []
  },
  "missing_information": [],
  "clarifying_question": null
}
[SemanticRouter] RAW_RESULT {'status': 'ready', 'route': 'domain', 'domain_request': 'thế cả tuần thì sao', 'template': {'action': None, 'source': 'none', 'template_id': None, 'selection_index': None, 'requirements': {}, 'extracted_keywords': []}, 'missing_information': [], 'clarifying_question': None}
[Gemini][call=1] START_STRUCTURED model=gemma-4-26b-a4b-it input_chars=114 temperature=0.0
[Gemini][call=1] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native_structured][call=1] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=1] STRUCTURED_RESULT {'topics': ['weather'], 'execution_mode': 'single', 'primary_intent': 'weather', 'dependencies': [], 'news_query': '', 'wiki_topic': ''}
[Gemini][call=1] START_STRUCTURED model=gemma-4-26b-a4b-it input_chars=172 temperature=0.0
[Gemini][call=1] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native_structured][call=1] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=1] STRUCTURED_RESULT {'location_text': 'hà nội', 'date_text': 'cả tuần', 'time_of_day_text': None, 'normalized_time': None, 'request_type_candidate': 'forecast'}
[WEATHER_PIPELINE] {"code": "llm1_result", "result": {"date_text": "cả tuần", "location_text": "hà nội", "normalized_time": null, "request_type_candidate": "forecast", "time_of_day_text": null}, "stage": "llm1_extraction", "status": "received"}
[WEATHER_PIPELINE] {"code": "unrecognized_date", "message": {"field": "date_text", "request_type_candidate": "forecast", "requested_text": "cả tuần"}, "stage": "validation", "status": "needs_clarification"}
[Gemini][call=2] START model=gemma-4-26b-a4b-it prompt_chars=2373 temperature=0.0
[Gemini][call=2] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native][call=2] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=2] RESULT Bạn muốn xem dự báo thời tiết cho tuần cụ thể nào?

[WEB][WORKFLOW_METRICS]
  - Topics: ['weather']
  - Timings: {'manager': 1.8975778999738395, 'weather': 3.32314170000609}
  - LLM usage [manager]:
    model: gemma-4-26b-a4b-it
    prompt_tokens: 499
    completion_tokens: 38
    thoughts_tokens: unknown
    total_tokens: 537
    time_to_first_token: 1.008527s
    time_to_first_visible: 1.008527s
    time_to_last_visible: 1.686530s
    visible_generation_duration: 0.678003s
    total_request_time: 1.750347s
    cached_tokens: unknown
    prefix_cache_hit: no
    cache_hit_ratio: unknown
    kv_cache_hit: not_exposed_by_gemini_api
    raw_usage_keys: ['cache_tokens_details', 'cached_content_token_count', 'candidates_token_count', 'candidates_tokens_details', 'prompt_token_count', 'prompt_tokens_details', 'thoughts_token_count', 'tool_use_prompt_token_count', 'tool_use_prompt_tokens_details', 'total_token_count', 'traffic_type']
  - LLM usage [weather]:
    call_1:
      model: gemma-4-26b-a4b-it
      prompt_tokens: 843
      completion_tokens: 48
      thoughts_tokens: unknown
      total_tokens: 891
      time_to_first_token: 1.195930s
      time_to_first_visible: 1.195930s
      time_to_last_visible: 2.007403s
      visible_generation_duration: 0.811474s
      total_request_time: 2.054290s
      cached_tokens: unknown
      prefix_cache_hit: no
      cache_hit_ratio: unknown
      kv_cache_hit: not_exposed_by_gemini_api
      raw_usage_keys: ['cache_tokens_details', 'cached_content_token_count', 'candidates_token_count', 'candidates_tokens_details', 'prompt_token_count', 'prompt_tokens_details', 'thoughts_token_count', 'tool_use_prompt_token_count', 'tool_use_prompt_tokens_details', 'total_token_count', 'traffic_type']
    call_2:
      model: gemma-4-26b-a4b-it
      prompt_tokens: 619
      completion_tokens: 13
      thoughts_tokens: unknown
      total_tokens: 632
      time_to_first_token: 1.129797s
      time_to_first_visible: 1.129797s
      time_to_last_visible: 1.129797s
      visible_generation_duration: 0.000000s
      total_request_time: 1.131915s
      cached_tokens: unknown
      prefix_cache_hit: no
      cache_hit_ratio: unknown
      kv_cache_hit: not_exposed_by_gemini_api
      raw_usage_keys: ['cache_tokens_details', 'cached_content_token_count', 'candidates_token_count', 'candidates_tokens_details', 'prompt_token_count', 'prompt_tokens_details', 'thoughts_token_count', 'tool_use_prompt_token_count', 'tool_use_prompt_tokens_details', 'total_token_count', 'traffic_type']
INFO:     127.0.0.1:62789 - "POST /api/chat HTTP/1.1" 200 OK
[SemanticRouter] START prompt_chars=6558
[Gemini][call=1] START model=gemma-4-26b-a4b-it prompt_chars=9272 temperature=0.0
[Gemini][call=1] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native][call=1] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=1] RESULT {
  "status": "ready",
  "route": "domain",
  "domain_request": "cho tuần này",
  "template": {
    "action": null,
    "source": "none",
    "template_id": null,
    "selection_index": null,
    "requirements": {},
    "extracted_keywords": []
  },
  "missing_information": [],
  "clarifying_question": null
}
[SemanticRouter] RAW_RESULT {'status': 'ready', 'route': 'domain', 'domain_request': 'cho tuần này', 'template': {'action': None, 'source': 'none', 'template_id': None, 'selection_index': None, 'requirements': {}, 'extracted_keywords': []}, 'missing_information': [], 'clarifying_question': None}
[Gemini][call=1] START_STRUCTURED model=gemma-4-26b-a4b-it input_chars=239 temperature=0.0
[Gemini][call=1] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native_structured][call=1] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=1] STRUCTURED_RESULT {'topics': ['weather'], 'execution_mode': 'single', 'primary_intent': 'weather', 'dependencies': [], 'news_query': '', 'wiki_topic': ''}
[Gemini][call=1] START_STRUCTURED model=gemma-4-26b-a4b-it input_chars=298 temperature=0.0
[Gemini][call=1] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native_structured][call=1] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=1] STRUCTURED_RESULT {'location_text': 'hà nội', 'date_text': 'tuần này', 'time_of_day_text': None, 'normalized_time': None, 'request_type_candidate': 'forecast'}
[WEATHER_PIPELINE] {"code": "llm1_result", "result": {"date_text": "tuần này", "location_text": "hà nội", "normalized_time": null, "request_type_candidate": "forecast", "time_of_day_text": null}, "stage": "llm1_extraction", "status": "received"}
[WEATHER_PIPELINE] {"code": "unrecognized_date", "message": {"field": "date_text", "request_type_candidate": "forecast", "requested_text": "tuần này"}, "stage": "validation", "status": "needs_clarification"}
[Gemini][call=2] START model=gemma-4-26b-a4b-it prompt_chars=2502 temperature=0.0
[Gemini][call=2] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native][call=2] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=2] RESULT Bạn muốn xem dự báo thời tiết cho tuần cụ thể nào?

[WEB][WORKFLOW_METRICS]
  - Topics: ['weather']
  - Timings: {'manager': 1.8013596999808215, 'weather': 3.2121059000492096}
  - LLM usage [manager]:
    model: gemma-4-26b-a4b-it
    prompt_tokens: 528
    completion_tokens: 38
    thoughts_tokens: unknown
    total_tokens: 566
    time_to_first_token: 0.954003s
    time_to_first_visible: 0.954003s
    time_to_last_visible: 1.609296s
    visible_generation_duration: 0.655293s
    total_request_time: 1.663851s
    cached_tokens: unknown
    prefix_cache_hit: no
    cache_hit_ratio: unknown
    kv_cache_hit: not_exposed_by_gemini_api
    raw_usage_keys: ['cache_tokens_details', 'cached_content_token_count', 'candidates_token_count', 'candidates_tokens_details', 'prompt_token_count', 'prompt_tokens_details', 'thoughts_token_count', 'tool_use_prompt_token_count', 'tool_use_prompt_tokens_details', 'total_token_count', 'traffic_type']
  - LLM usage [weather]:
    call_1:
      model: gemma-4-26b-a4b-it
      prompt_tokens: 877
      completion_tokens: 48
      thoughts_tokens: unknown
      total_tokens: 925
      time_to_first_token: 1.084421s
      time_to_first_visible: 1.084421s
      time_to_last_visible: 1.946375s
      visible_generation_duration: 0.861954s
      total_request_time: 1.995405s
      cached_tokens: unknown
      prefix_cache_hit: no
      cache_hit_ratio: unknown
      kv_cache_hit: not_exposed_by_gemini_api
      raw_usage_keys: ['cache_tokens_details', 'cached_content_token_count', 'candidates_token_count', 'candidates_tokens_details', 'prompt_token_count', 'prompt_tokens_details', 'thoughts_token_count', 'tool_use_prompt_token_count', 'tool_use_prompt_tokens_details', 'total_token_count', 'traffic_type']
    call_2:
      model: gemma-4-26b-a4b-it
      prompt_tokens: 653
      completion_tokens: 13
      thoughts_tokens: unknown
      total_tokens: 666
      time_to_first_token: 1.052944s
      time_to_first_visible: 1.052944s
      time_to_last_visible: 1.052944s
      visible_generation_duration: 0.000000s
      total_request_time: 1.061286s
      cached_tokens: unknown
      prefix_cache_hit: no
      cache_hit_ratio: unknown
      kv_cache_hit: not_exposed_by_gemini_api
      raw_usage_keys: ['cache_tokens_details', 'cached_content_token_count', 'candidates_token_count', 'candidates_tokens_details', 'prompt_token_count', 'prompt_tokens_details', 'thoughts_token_count', 'tool_use_prompt_token_count', 'tool_use_prompt_tokens_details', 'total_token_count', 'traffic_type']
INFO:     127.0.0.1:52575 - "POST /api/chat HTTP/1.1" 200 OK
[Gemini][call=1] START_STRUCTURED model=gemma-4-26b-a4b-it input_chars=379 temperature=0.0
[Gemini][call=1] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native_structured][call=1] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=1] STRUCTURED_RESULT {'topics': ['weather'], 'execution_mode': 'single', 'primary_intent': 'weather', 'dependencies': [], 'news_query': '', 'wiki_topic': ''}
[Gemini][call=1] START_STRUCTURED model=gemma-4-26b-a4b-it input_chars=461 temperature=0.0
[Gemini][call=1] HTTP_STREAM_ATTEMPT config=1/1 attempt=1/3
[LLM_CACHE][source=gemini_native_structured][call=1] cached_tokens=unknown cache_hit_ratio=unknown saved_tokens_estimated=unknown
[Gemini][call=1] STRUCTURED_RESULT {'location_text': 'hà nội', 'date_text': '3 ngày tới', 'time_of_day_text': None, 'normalized_time': None, 'request_type_candidate': 'forecast'}
[WEATHER_PIPELINE] {"code": "llm1_result", "result": {"date_text": "3 ngày tới", "location_text": "hà nội", "normalized_time": null, "request_type_candidate": "forecast", "time_of_day_text": null}, "stage": "llm1_extraction", "status": "received"}
[WEATHER_PIPELINE] {"code": "ready_for_redis", "message": {}, "stage": "validation", "status": "ready_for_redis"}
[WEATHER_REDIS] tool=get_weather_forecast location_id='ha_noi' ok=True snapshot_id='20260720T015908815675Z' lookup_ms=41.37
[WEATHER_PIPELINE] {"code": "completed", "message": "", "stage": "redis", "status": "completed"}

Thông tin được ghép thành document và embedding:
Tên bài nguyên bản.
Tên bài không dấu đã chuẩn hóa.
Tên nghệ sĩ nguyên bản.
Tên nghệ sĩ đã chuẩn hóa.
Tags cấu hình và tags từ YouTube, tối đa 20.
Ngôn ngữ.
Thể loại/phiên bản video như official MV.
Genres và moods nếu được cấu hình.