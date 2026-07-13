# Historical Execution Plans: HTML Visualization va Template Agent

> NON-NORMATIVE: tài liệu lịch sử. Kiến trúc hiện hành nằm tại
> [00_canonical_template_architecture.md](00_canonical_template_architecture.md).
> Các phần bên dưới có thể chứa mode/router cũ và không được dùng làm source
> of truth cho flow Semantic Router hiện tại.

Tai lieu nay chia nho brainstorm `html_visualization_template_agent_brainstorm.md` thanh cac plan thuc thi. Nguyen tac la bam sat cac quyet dinh da chot:

- Visualization Orchestrator phai mong, chi route luong visualization.
- Existing template path khong goi LLM.
- Template Agent la workflow co dinh, LLM chi o mot so node.
- Khong dung open-ended autonomous tool-calling agent trong MVP.
- Base template la fillable code-base co `contract.json`.
- Phuong an chot de tao template la fill base contract bang `fill_plan.json`.
- Renderer, assembler, validator la deterministic.
- Data co field nao thi hien field do, thieu field nao thi an field/component do.

## Plan 0: Dieu kien nen tang va convention

Muc tieu:

- Dam bao tat ca plan sau dung chung contract, naming va runtime.

Phu thuoc:

- Weather Agent da tra `weather_data` dang structured envelope.
- Moi lenh Python/test nen chay trong conda env `LumiMultiAgent`.

Quy uoc runtime:

```text
conda run -n LumiMultiAgent python ...
```

Neu chay test tu repo root, can set:

```text
PYTHONPATH=D:\RAG_ManageAgent_Lumi\code
```

Tren PowerShell:

```text
$env:PYTHONPATH='D:\RAG_ManageAgent_Lumi\code'
conda run -n LumiMultiAgent python -m pytest code\tests
```

Anh huong cua Weather Agent hien tai:

```text
- Weather Agent dang dung LangChain tool calling la phu hop.
- Weather Agent van la domain agent, khong sinh HTML.
- Weather Agent cung cap structured weather_data cho visualization layer.
- Template Agent la workflow rieng, khong thay the Weather Agent.
- Viec dung conda env LumiMultiAgent khong anh huong thiet ke, chi anh huong cach chay/test.
```

Ket qua can co:

- Ghi ro trong README/dev docs sau nay rang moi test/dev command dung `LumiMultiAgent`.
- Khong cai dependency vao global Python.

## Plan 1: Weather data contract cho visualization

Muc tieu:

- Chot contract weather data lam input cho template/render.

Da co:

```text
weather_data:
  domain
  schema_version
  data_type
  location
  data.location
  data.current
  data.forecast
  source.provider
  source.tools_used
  available_fields
```

Can tiep tuc:

1. Ghi schema mau cho:

```text
weather.current.v1
weather.forecast.v1
weather.combined.v1
weather.error.v1
weather.empty.v1
```

2. Tao sample data:

```text
schemas/weather.current.v1/sample.json
schemas/weather.forecast.v1/sample.json
schemas/weather.combined.v1/sample.json
```

3. Tao utility deterministic:

```text
inspect_actual_data(agent_data)
```

Output cua `inspect_actual_data`:

```json
{
  "domain": "weather",
  "schema_version": "weather.combined.v1",
  "data_type": "combined",
  "available_fields": [],
  "has_current": true,
  "has_forecast": true
}
```

Acceptance criteria:

- Khong parse `weather_answer` de lay data.
- Template/render chi doc structured `weather_data`.
- Thieu field thi report vao `available_fields`, khong coi la loi.

## Plan 2: Existing template path

Muc tieu:

- Ho tro render nhanh bang complete template co san.
- Khong goi Template Agent va khong goi LLM.

Luon di:

```text
Domain result
-> Visualization Orchestrator
-> Template Registry lookup/recommend
-> Renderer
-> output HTML
```

Can implement:

1. Thu muc:

```text
templates/
  weather/
    weather_basic/
      template.html
      metadata.json
```

2. Registry deterministic:

```text
lookup_template(template_id)
recommend_templates(domain, schema_version, available_fields, filters)
read_template_metadata(template_id)
```

3. Renderer deterministic:

```text
render_template(template_html, answer, data)
save_visualization_output(html)
```

4. Policy:

```text
Neu user da chon template_id:
  Registry lookup template_id

Neu user chua chon template_id:
  Registry recommend bang metadata score

Neu existing template du phu hop:
  render luon

Khong goi Template Agent trong path nay.
```

Acceptance criteria:

- `weather_basic` render duoc voi `weather.current.v1`.
- Missing field khong render ra `None`/`undefined`.
- Registry recommend khong dung LLM.

## Plan 3: Base template va component library

Muc tieu:

- Tao nen tang cho Template Agent tao template bang fill plan.

Can implement:

1. Base templates:

```text
base_templates/
  base_dashboard/
    base.html
    metadata.json
    contract.json
```

2. Base contract phai co:

```text
slots
parameters
hooks
missing_field_policy
```

Vi du:

```json
{
  "id": "base_dashboard",
  "slots": {
    "hero": {"accepts": ["metric_hero"], "max_components": 1},
    "metrics": {"accepts": ["metric_card"], "max_components": 6},
    "chart_area": {"accepts": ["line_chart"], "max_components": 1}
  },
  "parameters": {
    "title": "string",
    "theme": ["light_blue", "neutral", "dark"],
    "density": ["compact", "comfortable"]
  },
  "hooks": {
    "after_chart": {"accepts": ["source_note"]}
  },
  "missing_field_policy": "hide_component"
}
```

3. Components:

```text
components/
  temperature_hero/
    component.html
    metadata.json
  metric_card/
    component.html
    metadata.json
  forecast_chart/
    component.html
    metadata.json
```

4. Component metadata phai co:

```text
id
component_type
required_fields
supports_domains
accepted_slots
needs_javascript
external_network
```

5. Component filter:

```text
filter_visible_components(available_fields, candidate_components)
```

Policy:

```text
Component du field -> visible_components
Component thieu field -> hidden_components
Khong phan loai hard/soft/optional trong MVP
Khong fetch them data
Khong hoi user vi thieu field
```

Acceptance criteria:

- Co it nhat `base_dashboard`.
- Co it nhat 3 weather components.
- `filter_visible_components` chi dua component du field vao visible list.

## Plan 4: Deterministic assembler va validator

Muc tieu:

- Bien `base + fill_plan + components` thanh `template.html` ma khong dung LLM.

Can implement:

1. Fill plan schema:

```json
{
  "base_template": "base_dashboard",
  "parameters": {
    "title": "Thoi tiet Ha Noi",
    "theme": "light_blue",
    "density": "comfortable"
  },
  "slots": {
    "hero": ["temperature_hero"],
    "metrics": ["humidity_card", "wind_card"],
    "chart_area": ["forecast_chart"]
  },
  "hooks": {
    "after_chart": ["source_note"]
  },
  "hidden_components": ["uv_badge"]
}
```

2. Assembler:

```text
assemble_template_from_base(base_template, base_contract, fill_plan, components)
```

3. Validator:

```text
validate_fill_plan(fill_plan, base_contract, visible_components)
validate_template_syntax(template_html)
validate_placeholders(template_html, schema)
validate_security(template_html)
```

Guardrails:

```text
- Khong external script
- Khong iframe
- Khong form submit ra ngoai
- Khong inline event handler
- Khong network request
- Component trong slots/hooks phai nam trong visible_components
- Slot/hook phai ton tai trong base contract
- Parameter phai hop le theo contract
```

Acceptance criteria:

- Invalid component id bi reject.
- Component hidden khong duoc assemble vao slots/hooks.
- Template output render duoc voi sample data.

## Plan 5: Template Agent workflow

Muc tieu:

- Implement Template Agent theo workflow co dinh, LLM o mot so node.
- Khong dung open-ended autonomous tool-calling agent trong MVP.

Workflow chot:

```text
extract_requirements_with_llm
-> inspect_actual_data
-> search_complete_templates_by_metadata
-> search_base_templates_by_metadata
-> decide_template_strategy_with_llm
-> resolve_or_create_base_from_strategy
-> search_components_by_metadata
-> filter_visible_components
-> select_components_with_llm
-> generate_todo_list_with_llm
-> generate_fill_plan_with_llm
-> assemble_template_from_base
-> deterministic_validate
-> render_preview
-> save_template
-> return template artifact
```

LLM nodes:

```text
llm_extract_requirements
llm_decide_template_strategy
llm_generate_new_base
llm_select_components
llm_generate_todo_list
llm_generate_fill_plan
llm_repair_template
```

Strategy options:

```text
use_existing_template
fork_or_customize_existing_template
use_base_as_is
use_base_with_template_level_adjustments
create_new_base_template
one_off_blank_fallback
```

Important policy:

```text
Neu base phu hop nhung can sua:
  tao template-level adjustments
  khong sua base goc
  khong luu thanh base reusable moi

Neu khong co base phu hop:
  create_new_base_template
  base moi phai fillable
  base moi co base.html, contract.json, metadata.json
  luu vao base library
```

LLM constraints:

```text
- Chi quyet dinh dua tren context duoc truyen vao.
- Khong bia template_id/base_id/component_id.
- Neu chon existing id thi id phai nam trong candidate list.
- selected_components phai nam trong visible_components.
- hidden_components khong duoc dua vao slots/hooks.
```

Acceptance criteria:

- LLM strategy output la JSON co schema ro.
- Workflow reject output vi pham id/candidate constraints.
- Existing template match cao thi khong tao base/template moi.
- Base partial match thi adjustment chi o cap template/output.

## Plan 6: Visualization Orchestrator mong

Muc tieu:

- Orchestrator chi route, khong quyet dinh chi tiet template.
- Trong LangGraph chinh chi them mot node visualization la `visualize`; Visualization Orchestrator la service/workflow noi bo duoc `visualize` node goi, khong phai node graph rieng.

LangGraph target:

```text
input_router
  |-> manager_classify -> weather/news/wiki/parallel/sequential -> aggregate -> visualize -> END
  |-> visualize -> END
```

Y nghia:

```text
- Duong 1 dung cho cau hoi domain moi. Sau khi aggregate co cau tra loi/data, visualize auto recommend/render template.
- Duong 2 dung cho visualization follow-up command nhu "chon mau 2", "doi template", "tao dashboard nen xanh".
- Van chi co mot LangGraph node `visualize`; node nay nhin state de biet dang auto render hay render lai/customize.
- `input_router` khong goi LLM mac dinh trong MVP; no bat command dua tren context/session va pattern don gian truoc.
```

State can them:

```text
visualization_request
visualization_output
visualization_html_path
last_domain_result
available_templates
pending_visualization_action
```

Routing policy:

```text
Neu input la cau hoi domain moi:
  input_router -> manager_classify

Neu input la visualization command va co last_domain_result:
  input_router -> visualize

Neu input la visualization command nhung chua co last_domain_result:
  input_router -> visualize
  visualize tra message can hoi domain truoc
```

Orchestrator lam:

```text
- Nhan domain_result va visualization_request.
- Voi auto render, doc domain/schema_version/available_fields tu domain_result.
- Goi recommend_templates(domain, schema_version, available_fields, filters) neu user chua chon template.
- Neu co template_id: Registry lookup.
- Neu khong co template_id va mode auto/choose: Registry recommend.
- Neu existing template du phu hop: Renderer render.
- Neu create/customize hoac khong co template phu hop: goi Template Agent workflow.
- Nhan template artifact tu Template Agent.
- Goi Renderer deterministic de render final output.
```

Orchestrator khong lam:

```text
- doc base contract
- chon component
- quyet dinh slot/hook
- tao to-do list
- tao fill plan
- assemble template
- repair template
```

Acceptance criteria:

- Existing template path khong goi LLM.
- Create/customize path goi Template Agent dung mot workflow.
- Orchestrator co test rieng cho lookup/recommend/create routing.
- LangGraph co `input_router` va mot node `visualize`.
- `visualize` xu ly duoc ca auto render sau aggregate va follow-up command render lai tu `last_domain_result`.
- Cau "chon mau 2" khong di qua Manager Agent khi session dang cho/chua template choice.

## Plan 7: CLI/UI integration

Muc tieu:

- Dua visualization vao app hien tai theo cach nho, khong pha workflow RAG.

MVP options:

```text
--visualize
--template weather_basic
--visualization-mode auto|choose|create
```

Luon de xuat:

```text
Weather Agent -> weather_answer + weather_data
Visualization Orchestrator -> output html path
CLI hien duong dan output
```

UX chatbot de xuat:

```text
Lan dau user hoi domain:
  Bot tra answer text + tu dong render HTML bang template recommend.
  Bot hien path output va 1-3 template options neu co.

Neu user nhap "chon mau 2":
  input_router nhan la visualization command.
  visualize dung last_domain_result + available_templates[1].
  Render lai, khong goi Manager/Weather/Aggregator.

Neu user mo ta template:
  input_router nhan la visualization command.
  visualize goi Orchestrator voi mode create/customize.
  Orchestrator goi Template Agent workflow neu can.
```

Acceptance criteria:

- User hoi weather current va render `weather_basic`.
- User chon template_id thi lookup/render.
- User khong chon template_id thi auto recommend/render.
- User follow-up "chon mau 2" render lai tu data lan truoc, khong goi Weather Agent lai.

## Plan 8: Template Agent LLM runtime va prompt assets

Muc tieu:

- Cho phep Template Agent goi LLM that de xu ly create/customize template.
- Prompt khong hard-code trong Python logic chinh.
- Moi output tu LLM phai la JSON va phai qua schema/candidate validation truoc khi dung.

Prompt assets de xuat:

```text
code/rag_manager/visualization/assets/prompts/template_agent/
  extract_requirements.txt
  decide_template_strategy.txt
  select_components.txt
  generate_todo_list.txt
  generate_fill_plan.txt
  repair_template.txt
```

Runtime modules de xuat:

```text
visualization/prompt_loader.py
visualization/llm_output.py
visualization/template_agent.py
```

Luon de xuat:

```text
TemplateAgentWorkflow.llm_* wrapper
  -> load prompt file
  -> inject candidate lists / data summary / user request
  -> call LLM client
  -> parse JSON
  -> validate schema
  -> validate candidate ids
  -> return structured dict/list
```

Guardrails bat buoc:

- LLM khong duoc tao `template_id`, `base_template`, `component_id` ngoai candidate list.
- LLM khong duoc tu them data field khong co trong `available_fields`.
- Fill plan phai qua `validate_fill_plan`.
- Template HTML phai qua `validate_template_syntax`, `validate_placeholders`, `validate_security`.
- Repair loop co gioi han 1-2 lan, fail thi tra error co kiem soat.

Acceptance criteria:

- Prompt files ton tai va doc duoc bang loader.
- Fake LLM client tra plain JSON va fenced JSON deu parse duoc.
- Invalid JSON bi reject.
- Invalid candidate id bi reject.
- Repair loop chi chay trong gioi han va khong save artifact neu validator fail.

## Plan 9: Test strategy

Muc tieu:

- Dam bao toc do nhanh, deterministic path on dinh, LLM path co guardrails.

Test groups:

```text
weather_data_contract tests
registry lookup/recommend tests
filter_visible_components tests
fill_plan validation tests
assembler tests
renderer tests
template_agent strategy JSON tests
template_agent prompt loader va LLM runtime fake client tests
orchestrator routing tests
CLI integration tests
```

Commands:

```text
$env:PYTHONPATH='D:\RAG_ManageAgent_Lumi\code'
conda run -n LumiMultiAgent python -m pytest code\tests
```

Acceptance criteria:

- Existing tests van pass.
- Existing template render path test khong can LLM.
- Template Agent tests co fake LLM output de validate workflow.
- Template Agent LLM runtime tests co fake LLM client de validate prompt loader, JSON parser, candidate guardrails va repair loop.

## De xuat thu tu lam viec

```text
1. Plan 1 - Weather data contract/schema/sample
2. Plan 2 - Existing template path + renderer
3. Plan 3 - Base/component library
4. Plan 4 - Assembler + validator
5. Plan 6 - Thin Orchestrator cho existing template path
6. Plan 5 - Template Agent workflow
7. Plan 7 - CLI/UI integration
8. Plan 8 - Template Agent LLM runtime + prompt assets
9. Plan 9 - Full tests/QA
```

Ly do thu tu nay:

```text
- Render deterministic co truoc thi nhanh thay gia tri.
- Template Agent de sau khi registry/base/component/assembler da san sang.
- LLM path chi them vao khi deterministic foundation da on dinh.
```

## M10. Cap nhat Template Agent theo luong hoi thoai

- [ ] Them `pending_template_state` vao GraphState va CLI session context.
- [ ] Requirement gate/extractor chay truoc inspect va candidate search.
- [ ] Neu thieu mo ta, tra `missing_template_requirements`, hoi lai bang plain language.
- [ ] Ho tro merge cau tra loi o luot sau, cancel va gioi han clarification rounds.
- [ ] Inspect domain/schema/available_fields sau khi requirements ready.
- [ ] Search candidate theo requirements + domain/schema va chi truyen top-K.
- [ ] Gop strategy, component selection va todo list vao LLM call thu hai.
- [ ] Giu assembly, validation, repair, render va save deterministic.

Definition of done M10: cau "toi muon tao template moi" khong tao artifact; cau tra
loi bo sung duoc merge; domain data cu khong duoc tu dong dung khi mo ta con thieu;
candidate IDs do LLM chon phai nam trong registry candidates.

## M11. Template change options

- [ ] `change_template` hien danh sach compatible templates va action `create_new_template`.
- [ ] Luu pseudo-item `__create_new_template__` trong available template state.
- [ ] So thu tu cua pseudo-item route sang requirement flow, khong lookup nhu template that.
- [ ] LLM1 nhan selection va modifications trong cung structured output.
- [ ] Modification duoc validate va ap dung qua design tokens/fill plan, khong sinh CSS tu do.
- [ ] Ho tro selection tu nhien: so, "mau thu hai", template ID, ten template.
- [ ] Ho tro selection kem thay doi: "mau 2, doi nen hong".
- [ ] Them `interpret_template_action` prompt/schema cho LLM1.
- [ ] LLM1 nhan raw user message + state + candidate metadata; code chi validate
  action va goi tools.
- [ ] Parser deterministic chi con la fallback offline, khong la semantic source
  of truth trong runtime co LLM.
- [ ] `create/customize` khong duoc return som khi strategy la existing_template
  neu request co mo ta thay doi.
- [ ] Ap dung style modification bang allow-listed tokens, sau do render va save
  preview artifact.
