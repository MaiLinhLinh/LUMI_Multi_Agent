# Historical Tracking Plan: HTML Visualization Code Execution

> NON-NORMATIVE: tracker lịch sử. Kiến trúc hiện hành nằm tại
> [00_canonical_template_architecture.md](00_canonical_template_architecture.md).
> Những mục cũ về input_router, mode và interpret_template_action chỉ giữ để
> đối chiếu lịch sử.

Tai lieu nay bien `html_visualization_execution_plans.md` thanh tracker thuc thi code. Moi item nen duoc tick theo PR/commit nho, co test di kem, va chi chuyen sang phase sau khi acceptance criteria cua phase truoc da pass.

## Status legend

```text
[ ] Not started
[/] In progress
[x] Done
[!] Blocked
```

## Runtime convention

```powershell
$env:PYTHONPATH='D:\RAG_ManageAgent_Lumi\code'
conda run -n LumiMultiAgent python -m pytest code\tests
```

Repo hien tai da co:

```text
code/rag_manager/agents/weather.py
code/rag_manager/state.py
code/rag_manager/graph.py
code/main.py
code/tests/
```

Chua co module visualization. De xuat them namespace moi:

```text
code/rag_manager/visualization/
  __init__.py
  inspector.py
  registry.py
  renderer.py
  components.py
  assembler.py
  validator.py
  orchestrator.py
  template_agent.py
```

Asset/data nen dat trong package de test doc duoc on dinh:

```text
code/rag_manager/visualization/assets/
  schemas/
  templates/
  base_templates/
  components/
```

Output runtime:

```text
outputs/visualizations/
```

## Milestone M0: Baseline va convention

Muc tieu: dam bao nen tang test/dev san sang truoc khi them visualization.

Tasks:

- [x] M0.1 Chay full test hien tai de lay baseline.
  - Command: `conda run -n LumiMultiAgent python -m pytest code\tests`
  - Ket qua: `62 passed` sau khi them package/test visualization skeleton.
  - Done khi: ghi lai pass/fail hien tai vao commit/PR notes.
- [x] M0.2 Tao package rong `code/rag_manager/visualization/__init__.py`.
  - Test: import package trong `code/tests/test_visualization_package.py`.
  - Done khi: import khong pha test hien tai.
- [x] M0.3 Chot path assets va outputs.
  - File du kien: `visualization/paths.py` neu can helper path.
  - Done khi: test co the resolve asset dir va output dir khong phu thuoc cwd.

Exit criteria:

- [x] Existing tests khong fail them.
- [x] Package visualization import duoc.

## Milestone M1: Weather data contract va inspector

Muc tieu: chot input structured cho render, khong parse `weather_answer`.

Tasks:

- [x] M1.1 Tao schema/sample files.
  - Files:
    - `assets/schemas/weather.current.v1/sample.json`
    - `assets/schemas/weather.forecast.v1/sample.json`
    - `assets/schemas/weather.combined.v1/sample.json`
    - `assets/schemas/weather.error.v1/sample.json`
    - `assets/schemas/weather.empty.v1/sample.json`
  - Done khi: samples co `domain`, `schema_version`, `data_type`, `data`, `available_fields`.
- [x] M1.2 Implement `inspect_actual_data(agent_data)`.
  - File: `visualization/inspector.py`
  - Output toi thieu: `domain`, `schema_version`, `data_type`, `available_fields`, `has_current`, `has_forecast`.
  - Done khi: function chi doc structured `weather_data`.
- [x] M1.3 Test inspector voi current/forecast/combined/error/empty.
  - File: `code/tests/test_visualization_inspector.py`
  - Done khi: missing fields khong bi coi la loi.
- [x] M1.4 Neu can, expose helper lay weather available fields tu envelope hien tai.
  - File lien quan: `agents/weather.py`
  - Ket qua: chua can them helper moi vi envelope hien tai da co `available_fields`.
  - Done khi: khong doi behavior answer hien tai.

Exit criteria:

- [x] `inspect_actual_data` khong dung text answer.
- [x] Samples render/test duoc tu package assets.

Ket qua test M1:

```text
conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_package.py code\tests\test_visualization_inspector.py
11 passed

conda run -n LumiMultiAgent python -m pytest code\tests
69 passed
```

## Milestone M2: Existing template path va renderer deterministic

Muc tieu: render HTML nhanh bang complete template co san, khong LLM.

Tasks:

- [x] M2.1 Tao complete template `weather_basic`.
  - Files:
    - `assets/templates/weather/weather_basic/template.html`
    - `assets/templates/weather/weather_basic/metadata.json`
  - Done khi: metadata co `id`, `kind`, `domain`, `schema_versions`, `required_fields`, `optional_fields`.
- [x] M2.2 Implement registry lookup/read metadata.
  - File: `visualization/registry.py`
  - Functions:
    - `lookup_template(template_id)`
    - `read_template_metadata(template_id)`
    - `list_templates(domain=None)`
  - Done khi: invalid id tra loi co kiem soat, khong crash kho hieu.
- [x] M2.3 Implement recommend deterministic.
  - File: `visualization/registry.py`
  - Function: `recommend_templates(domain, schema_version, available_fields, filters=None)`
  - Done khi: score dua tren metadata, khong goi LLM.
- [x] M2.4 Implement renderer.
  - File: `visualization/renderer.py`
  - Functions:
    - `render_template(template_html, answer, data)`
    - `save_visualization_output(html, output_dir=None)`
  - Done khi: missing fields bi an, khong render `None`/`undefined`.
- [x] M2.5 Test existing template path.
  - Files:
    - `code/tests/test_visualization_registry.py`
    - `code/tests/test_visualization_renderer.py`
  - Done khi: `weather_basic` render duoc voi sample current/combined.

Exit criteria:

- [x] User chon `weather_basic` thi lookup/render duoc.
- [x] User khong chon template thi recommend ra `weather_basic` khi compatible.
- [x] Khong co LLM dependency trong milestone nay.

Ket qua test M2:

```text
conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_registry.py code\tests\test_visualization_renderer.py
13 passed

conda run -n LumiMultiAgent python -m pytest code\tests
82 passed
```

## Milestone M3: Base template va component library

Muc tieu: co nen tang tao template bang fill plan.

Tasks:

- [x] M3.1 Tao `base_dashboard`.
  - Files:
    - `assets/base_templates/base_dashboard/base.html`
    - `assets/base_templates/base_dashboard/metadata.json`
    - `assets/base_templates/base_dashboard/contract.json`
  - Done khi: contract co `slots`, `parameters`, `hooks`, `missing_field_policy`.
- [x] M3.2 Tao toi thieu 3 weather components.
  - Suggested components:
    - `temperature_hero`
    - `metric_card`
    - `forecast_chart`
    - optional: `source_note`
  - Done khi: moi component co `component.html` va `metadata.json`.
- [x] M3.3 Implement component registry/search.
  - File: `visualization/components.py`
  - Functions:
    - `list_components(filters=None)`
    - `read_component(component_id)`
    - `search_components_by_metadata(...)`
- [x] M3.4 Implement `filter_visible_components`.
  - File: `visualization/components.py`
  - Done khi: component thieu required fields vao `hidden_components`.
- [x] M3.5 Tests cho base/component metadata va visible filter.
  - File: `code/tests/test_visualization_components.py`

Exit criteria:

- [x] Co `base_dashboard` hop le.
- [x] Co it nhat 3 components weather hop le.
- [x] Visible filter deterministic va khong fetch them data.

Ket qua test M3:

```text
conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_components.py
7 passed

conda run -n LumiMultiAgent python -m pytest code\tests
89 passed
```

## Milestone M4: Assembler va validator deterministic

Muc tieu: bien `base + fill_plan + components` thanh `template.html` an toan.

Tasks:

- [x] M4.1 Dinh nghia fill plan schema dang Python validation.
  - File: `visualization/assembler.py` hoac `visualization/schemas.py`
  - Done khi: reject missing `base_template`, invalid slot/hook/parameter.
- [x] M4.2 Implement `validate_fill_plan`.
  - File: `visualization/validator.py`
  - Done khi: component hidden/unknown khong duoc vao slots/hooks.
- [x] M4.3 Implement `assemble_template_from_base`.
  - File: `visualization/assembler.py`
  - Done khi: base placeholders/slots duoc thay bang component snippets.
- [x] M4.4 Implement security/template validators.
  - File: `visualization/validator.py`
  - Functions:
    - `validate_template_syntax`
    - `validate_placeholders`
    - `validate_security`
  - Guardrails: no external script, iframe, external form submit, inline event handler, network request.
- [x] M4.5 Tests assembler/validator.
  - Files:
    - `code/tests/test_visualization_assembler.py`
    - `code/tests/test_visualization_validator.py`

Exit criteria:

- [x] Invalid component id bi reject.
- [x] Hidden component bi reject neu duoc assemble.
- [x] Template assemble render duoc voi sample data.

Ket qua test M4:

```text
conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_validator.py code\tests\test_visualization_assembler.py
18 passed

conda run -n LumiMultiAgent python -m pytest code\tests
107 passed
```

## Milestone M5: Thin Visualization Orchestrator

Muc tieu: route visualization path, khong chon component/slot/fill plan.

Tasks:

- [x] M5.1 Dinh nghia request/result models nhe.
  - File: `visualization/orchestrator.py`
  - Fields de xuat: `template_id`, `mode`, `domain_result`, `output_path`.
- [x] M5.2 Implement existing template routing.
  - Path:
    - co `template_id` -> lookup -> render
    - khong co `template_id`, mode `auto` -> recommend -> render
  - Done khi: khong goi Template Agent neu existing template du phu hop.
- [x] M5.3 Them placeholder path cho create/customize.
  - Done khi: mode `create/customize` goi interface `TemplateAgentWorkflow` nhung co the stub trong milestone nay.
- [x] M5.4 Tests routing.
  - File: `code/tests/test_visualization_orchestrator.py`

Exit criteria:

- [x] Existing template path khong goi LLM.
- [x] Orchestrator khong doc base contract/chon component.

Ket qua test M5:

```text
conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_orchestrator.py
6 passed

conda run -n LumiMultiAgent python -m pytest code\tests
113 passed
```

## Milestone M6: LangGraph visualization integration

Muc tieu: them visualization vao graph ro rang, chi co mot node LangGraph `visualize`, xu ly duoc ca auto render lan dau va follow-up command.

Target graph:

```text
input_router
  |-> manager_classify -> weather/news/wiki/parallel/sequential -> aggregate -> visualize -> END
  |-> visualize -> END
```

Nguyen tac:

```text
- `visualize` la LangGraph node duy nhat cho visualization.
- Visualization Orchestrator la service/function duoc `visualize` node goi ben trong, khong phai graph node rieng.
- `input_router` quyet dinh user input la domain question moi hay visualization follow-up command.
- Follow-up command nhu "chon mau 2" khong duoc di qua Manager Agent.
```

Tasks:

- [x] M6.1 Bo sung state fields.
  - File: `code/rag_manager/state.py`
  - Fields de xuat:
    - `input_route`
    - `visualization_request`
    - `visualization_output`
    - `visualization_html_path`
    - `last_domain_result`
    - `available_templates`
    - `pending_visualization_action`
  - Done khi: TypedDict cho phep graph luu context visualization qua turns.
- [x] M6.2 Implement `input_router` node/function.
  - File: `code/rag_manager/graph.py`
  - Routing:
    - domain question -> `manager_classify`
    - visualization command + co `last_domain_result` -> `visualize`
    - visualization command + chua co `last_domain_result` -> `visualize` de tra message can hoi domain truoc
  - Pattern MVP:
    - `chon mau <n>`
    - `doi template`
    - `tao template ...`
    - `lam ... template ...`
- [x] M6.3 Them `visualize_node`.
  - File: `code/rag_manager/graph.py`
  - Behavior:
    - neu co domain result moi tu weather/news/wiki/aggregate -> auto render
    - neu co visualization command -> render lai/customize tu `last_domain_result`
    - neu thieu data -> tra visualization message/error co kiem soat
  - Done khi: node goi `VisualizationOrchestrator.run(...)`, khong tu implement registry/render chi tiet.
- [x] M6.4 Sua graph edges.
  - Thay:
    - `aggregate -> END`
  - Bang:
    - `aggregate -> visualize -> END`
  - Them:
    - `input_router -> manager_classify`
    - `input_router -> visualize`
  - Done khi: path domain va path follow-up deu compile.
- [x] M6.5 Luu `last_domain_result` sau domain path.
  - Source:
    - weather: `weather_answer + weather_data`
    - sau nay news/wiki neu co structured visualization data
  - Done khi: follow-up "chon mau 2" khong can goi Weather Agent lai.
- [x] M6.6 Tests LangGraph routing.
  - File: `code/tests/test_visualization_graph_routing.py` hoac `code/tests/test_graph_workflow.py`
  - Cases:
    - domain question -> manager -> weather -> aggregate -> visualize
    - "chon mau 2" + `last_domain_result` -> visualize only
    - "chon mau 2" khong co `last_domain_result` -> visualize returns helpful message
    - auto render truyen `domain`, `schema_version`, `available_fields` vao orchestrator recommend path

Exit criteria:

- [x] LangGraph co `input_router` va mot node `visualize`.
- [x] Cau follow-up visualization khong bi route vao weather/news/wiki.
- [x] Auto render sau aggregate van bat buoc di qua `visualize`.
- [x] `visualize` khong import/call LLM truc tiep tren existing-template path.

Ket qua test M6:

```text
conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_graph_routing.py code\tests\test_graph_workflow.py
7 passed

conda run -n LumiMultiAgent python -m pytest code\tests
116 passed
```

## Milestone M7: CLI integration MVP

Muc tieu: dua visualization vao app hien tai voi thay doi nho.

Tasks:

- [x] M7.1 Xac dinh cach CLI hien tai nhan options.
  - File: `code/main.py`
  - Note: hien tai `main()` la interactive loop, chua thay `argparse`.
- [x] M7.2 Them lenh/options visualization nho nhat.
  - Options de xuat:
    - `--visualize`
    - `--template weather_basic`
    - `--visualization-mode auto|choose|create`
  - Neu giu interactive loop: co the them command config trong session truoc khi them argparse.
- [x] M7.3 Sau weather agent run, truyen `weather_answer + weather_data` vao orchestrator.
  - Files lien quan:
    - `code/main.py`
    - co the `rag_manager/graph.py` neu can state output.
- [x] M7.4 Hien duong dan output HTML cho user.
- [x] M7.5 Tests CLI/main integration.
  - File: `code/tests/test_main.py` hoac `code/tests/test_visualization_cli.py`

Exit criteria:

- [x] Hoi weather current va render `weather_basic`.
- [x] User chon template id thi lookup/render.
- [x] Auto mode recommend/render.

Ket qua M7:

```text
conda run -n LumiMultiAgent python -m pytest code\tests\test_main.py
16 passed

conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_graph_routing.py
3 passed

conda run -n LumiMultiAgent python -m pytest code\tests
117 passed
```

## Milestone M8: Template Agent workflow

Muc tieu: workflow co dinh, LLM chi o node ro rang.

Tasks:

- [x] M8.1 Tao interface workflow.
  - File: `visualization/template_agent.py`
  - Function/class de xuat: `TemplateAgentWorkflow.run(request)`.
- [x] M8.2 Implement deterministic backbone truoc, fake/stub LLM output trong test.
  - Nodes:
    - `inspect_actual_data`
    - `search_complete_templates_by_metadata`
    - `search_base_templates_by_metadata`
    - `search_components_by_metadata`
    - `filter_visible_components`
    - `assemble_template_from_base`
    - `deterministic_validate`
    - `render_preview`
    - `save_template`
- [x] M8.3 Them LLM wrapper functions co ten ro.
  - Suggested names:
    - `llm_extract_requirements`
    - `llm_decide_template_strategy`
    - `llm_select_components`
    - `llm_generate_todo_list`
    - `llm_generate_fill_plan`
    - `llm_repair_template`
  - Done khi: output JSON validate bang schema/candidate constraints.
- [x] M8.4 Enforce candidate constraints.
  - Done khi: LLM khong duoc bia `template_id`, `base_id`, `component_id`.
- [x] M8.5 Save generated template artifact.
  - Output:
    - `template.html`
    - `metadata.json`
    - optional `fill_plan.json`
  - Done khi: metadata co source base/components/todo/fill_plan/validation.
- [x] M8.6 Tests Template Agent voi fake LLM.
  - File: `code/tests/test_visualization_template_agent.py`

Exit criteria:

- [x] Existing template match cao thi workflow khong tao base/template moi.
- [x] Base partial match chi tao template-level adjustment.
- [x] Invalid LLM id output bi reject.

Ket qua M8:

```text
conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_template_agent.py
6 passed

conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_orchestrator.py
6 passed

conda run -n LumiMultiAgent python -m pytest code\tests
123 passed
```

## Milestone M8.5: Template Agent LLM runtime va prompt assets

Muc tieu: bien Template Agent tu fake/stub LLM sang co the goi LLM that, nhung prompt/output van duoc quan ly bang file va validate chat che.

Prompt assets:

```text
code/rag_manager/visualization/assets/prompts/template_agent/
  extract_requirements.txt
  decide_template_strategy.txt
  select_components.txt
  generate_todo_list.txt
  generate_fill_plan.txt
  repair_template.txt
```

Tasks:

- [x] M8.5.1 Tao prompt files cho tung LLM wrapper.
  - Files:
    - `assets/prompts/template_agent/extract_requirements.txt`
    - `assets/prompts/template_agent/decide_template_strategy.txt`
    - `assets/prompts/template_agent/select_components.txt`
    - `assets/prompts/template_agent/generate_todo_list.txt`
    - `assets/prompts/template_agent/generate_fill_plan.txt`
    - `assets/prompts/template_agent/repair_template.txt`
  - Done khi: moi prompt yeu cau JSON output ro schema, cam bia `template_id`, `base_template`, `component_id` ngoai candidate list.
- [x] M8.5.2 Implement prompt loader.
  - File de xuat: `visualization/prompt_loader.py`
  - Functions:
    - `load_template_agent_prompt(name)`
    - `render_prompt(template, variables)`
  - Done khi: missing prompt name loi co kiem soat, khong phu thuoc cwd.
- [x] M8.5.3 Them JSON output parser/validator cho LLM response.
  - File de xuat: `visualization/llm_output.py` hoac trong `template_agent.py`.
  - Functions:
    - `parse_llm_json_response(text)`
    - `validate_requirements_output(...)`
    - `validate_strategy_output(...)`
    - `validate_component_selection_output(...)`
    - `validate_fill_plan_output(...)`
  - Done khi: markdown fenced JSON, plain JSON deu parse duoc; invalid JSON tra loi co kiem soat.
- [x] M8.5.4 Noi LLM client vao cac wrapper `llm_*`.
  - File: `visualization/template_agent.py`
  - Input moi co the la `llm_client` hoac dung client trong request/settings tuy theo pattern repo.
  - Done khi: wrapper nao goi LLM that thi van qua prompt file + JSON parser + candidate validation.
- [x] M8.5.5 Implement repair loop co gioi han.
  - Suggested:
    - toi da 1-2 lan repair
    - repair chi nhan validation errors + candidate list + template hien tai
    - output van qua validator/security validator
  - Done khi: repair fail thi tra error co kiem soat, khong save artifact nguy hiem.
- [x] M8.5.6 Tests voi fake LLM client.
  - File: `code/tests/test_visualization_template_agent_llm_runtime.py`
  - Cases:
    - prompt loader doc du file dung
    - valid JSON strategy/fill_plan duoc accept
    - fenced JSON parse duoc
    - invalid JSON bi reject
    - LLM bia id bi reject
    - repair loop duoc goi khi validation fail

Exit criteria:

- [x] Prompt khong nam hard-code trong Python logic chinh.
- [x] Moi LLM wrapper doc prompt file rieng.
- [x] Moi LLM output la JSON va duoc validate schema/candidate truoc khi dung.
- [x] LLM khong the chon template/base/component ngoai candidate list.
- [x] Repair loop co gioi han va van qua security validator.
- [x] Tests fake LLM runtime pass.

Ket qua M8.5:

```text
conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_template_agent_llm_runtime.py
6 passed

conda run -n LumiMultiAgent python -m pytest code\tests\test_visualization_template_agent.py code\tests\test_visualization_orchestrator.py
12 passed

conda run -n LumiMultiAgent python -m pytest code\tests
129 passed
```

## Milestone M9: Full QA va regression suite

Muc tieu: dam bao deterministic path on dinh, LLM path co guardrails.

Tasks:

- [ ] M9.1 Gom test groups thanh suite visualization.
  - Test groups:
    - weather data contract
    - registry lookup/recommend
    - renderer
    - component filter
    - fill plan validation
    - assembler
    - security validator
    - orchestrator routing
    - CLI integration
    - template agent fake LLM
    - template agent prompt loader va LLM runtime fake client
- [ ] M9.2 Chay full tests.
  - Command: `conda run -n LumiMultiAgent python -m pytest code\tests`
- [ ] M9.3 Them README/dev note neu can.
  - Noi dung: conda env, PYTHONPATH, visualization flow, output path.
- [ ] M9.4 Manual smoke test.
  - Scenario: weather query -> output HTML -> mo file kiem tra khong blank.

Exit criteria:

- [ ] Existing tests pass.
- [ ] Visualization tests pass.
- [ ] HTML output duoc tao tai `outputs/visualizations/`.

## De xuat thu tu commit/PR nho

1. M0 package skeleton va baseline.
2. M1 schemas/samples/inspector.
3. M2 registry + renderer + `weather_basic`.
4. M3 base/components + visible filter.
5. M4 assembler/validator.
6. M5 orchestrator existing-template route.
7. M6 LangGraph input_router + visualize node.
8. M7 CLI integration.
9. M8 Template Agent workflow voi fake LLM tests.
10. M8.5 Template Agent prompt assets va LLM runtime.
11. M9 QA/docs.

## Definition of done chung

- [ ] Moi module moi co test focus rieng.
- [ ] Deterministic path khong import LLM client.
- [ ] Missing field duoc hide, khong render `None`/`undefined`.
- [ ] Asset metadata duoc validate truoc khi dung.
- [ ] Security validator reject HTML nguy hiem.
- [ ] LLM output trong Template Agent luon qua schema/candidate validation.

## Architecture update - conversational Template Agent

Implemented target flow:

- Requirement gate/extractor runs before domain inspection and registry search.
- Missing requirements return a plain-language question and a
  `pending_template_state` for the next turn.
- Existing domain data is used only after requirements are ready.
- Candidate search is constrained by domain/schema/fields and user requirements.
- Strategy decisions remain constrained to registry candidate IDs.
- Assembly, validation, rendering and saving remain deterministic.

Required regression scenarios:

1. Bare create request -> clarification, no HTML artifact.
2. Follow-up answer -> requirements merge, candidate search and generation.
3. Cancel during clarification -> pending state cleared.
4. Existing template selection -> unchanged deterministic path.

## Template change options update

- Added the `__create_new_template__` action item to choose-mode template lists.
- Selecting that item routes to the create-template requirement gate.
- Numeric selection remains context-aware and does not route through Manager Agent.
- The selected template list is persisted in session state for the next turn.
- Selection requests with a trailing modification are preserved as
  `modification_request` and routed to customization; the strategy prompt also
  receives the selected template ID and modification context.
- Added `interpret_template_action` as the LLM1 structured decision for choose
  mode; candidate ID/index validation remains deterministic after the LLM call.
- Kept the old parser only as an offline fallback when no LLM is configured.
- Fixed create/customize early return: an existing compatible template is now
  treated as a candidate/source when the user supplied requirements; the flow
  continues through assembly, style-token application, preview and save.
- [ ] Full command pass: `conda run -n LumiMultiAgent python -m pytest code\tests`.
