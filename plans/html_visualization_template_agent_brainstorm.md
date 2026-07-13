# Historical Brainstorm: HTML Visualization voi Base Nhe, Component Library va Template Metadata

> NON-NORMATIVE: brainstorm lịch sử. Quyết định kiến trúc hiện hành nằm tại
> [00_canonical_template_architecture.md](00_canonical_template_architecture.md).

## 1. Muc tieu

He thong hien tai tra loi nguoi dung bang text. Muc tieu mo rong la cho phep chatbot tao visualization bang HTML tu ket qua cua domain agent, vi du weather, news, wiki, report, comparison.

Huong thiet ke duoc de xuat:

```text
Domain Agent output
  -> answer text + structured data
  -> Visualization Orchestrator
  -> Template Registry lookup/recommend neu dung template co san
  -> Template Agent on-demand neu can tao hoac sua template
  -> Deterministic Renderer
  -> HTML output
```

Trong thiet ke nay, khong nen de LLM sinh HTML truc tiep moi lan user hoi. Renderer phai deterministic. LLM chi nen tham gia o cac buoc can hieu yeu cau, lap ke hoach template, sinh/chinh template, hoac repair khi validate fail.

## 2. Nguyen tac chinh

1. Domain Agent khong sinh HTML.

   Domain Agent chi phu trach tra loi domain va tra ve structured data.

2. Renderer khong dung LLM.

   Render la tac vu lap lai: `template + data + answer -> html`. Nen dung code binh thuong nhu Jinja2 hoac engine tuong duong.

3. Template Agent chi goi on-demand.

   Neu template co san va compatible thi render luon. Chi goi Template Agent khi user muon tao template moi, sua template, custom style/layout, hoac khong co template phu hop.

4. Uu tien base nhe va component library hon template lon.

   Khong nen dua mot base template rat dai vao prompt. Nen dung base skeleton ngan, component snippets, theme tokens va metadata de ghep theo nhu cau.

5. Chon template/base bang metadata truoc, khong de agent doc toan bo thu vien.

   Neu thu vien co nhieu template, agent khong nen mo tung file HTML de xem co phu hop khong. He thong nen search/scoring tren metadata truoc.

6. Validate va preview phai deterministic.

   LLM co the sua loi sau khi validate fail, nhung khong nen la validator chinh.

## 3. Kien truc de xuat

```text
User query
  |
  v
Manager Agent
  |
  v
Domain Agent
  - Weather Agent
  - News Agent
  - Wiki Agent
  - Other domain agents
  |
  v
Domain result
  - answer
  - domain
  - structured data
  - schema_version
  |
  v
Visualization Orchestrator
  |
  +-- Template Registry
  |     - existing complete templates
  |     - lookup by template_id
  |     - recommend existing templates by metadata
  |
  +-- Template Agent on-demand
  |     - LLM steps + deterministic tools
  |     - Base Template Registry
  |     - Component Registry
  |     - Metadata Search / Compatibility Scorer
  |     - Data Inspector / Component Filter
  |     - Fill Plan Generator
  |     - Assembler
  |
  v
HTML Renderer
  |
  v
outputs/visualizations/*.html
```

## 3.1 Ranh gioi trach nhiem

Visualization Orchestrator phai la lop dieu phoi mong. No chon duong xu ly visualization, khong quyet dinh chi tiet template duoc cau thanh nhu the nao.

Orchestrator nen lam:

```text
- Nhan domain_result va visualization request.
- Kiem tra user co template_id cu the khong.
- Neu co template_id: lookup template do trong Template Registry.
- Neu khong co template_id nhung mode la auto/choose: goi Template Registry recommend existing templates bang metadata scoring.
- Neu existing template du diem: goi Renderer.
- Neu user muon create/customize hoac khong co template phu hop: goi Template Agent workflow.
- Nhan template artifact tu Template Agent roi goi Renderer de render final output.
```

Orchestrator khong nen lam:

```text
- doc base contract
- chon component chi tiet
- quyet dinh slot/hook
- tao to-do list
- tao fill plan
- patch/assemble template
- repair template
```

Nhung viec chi tiet nay thuoc ve Template Agent workflow va deterministic tools cua no.

Phan vai de giu toc do nhanh va do chinh xac cao:

```text
Orchestrator:
  route luong visualization

Template Registry:
  lookup/recommend complete templates bang metadata, khong LLM

Template Agent workflow:
  create/customize template, generate to-do list va fill plan

Base/Component Registry:
  nam trong toolset cua Template Agent khi can tao/custom

Data Inspector / Component Filter:
  inspect actual data va filter visible components bang code

Assembler:
  base + fill_plan + components -> template.html, khong LLM

Renderer:
  template.html + data -> output.html, khong LLM
```

## 4. Cac loai asset template

### 4.1 Complete Templates

Day la template hoan chinh, co the render ngay neu data compatible.

Vi du:

```text
templates/
  weather/
    weather_basic/
      template.html
      metadata.json
    weather_forecast_dashboard/
      template.html
      metadata.json
```

Dung khi:

- user chon template cu the;
- system tu recommend template co score cao;
- template da compatible voi schema hien tai.

Neu dung complete template thi khong can Template Agent.

### 4.2 Base Templates nhe

Base template khong nen duoc hieu la mot file HTML mau chung chung. Base template nen la mot fillable code-base: co san layout, CSS, placeholder regions, slot, parameters va hooks de agent dien noi dung vao theo contract ro rang.

No la xuong song cho viec tao template moi, nhung agent khong nen sinh HTML tu blank page. Agent nen doc contract cua base, sau do dien slot, set parameter va gan component phu hop.

Vi du:

```text
base_templates/
  base_card/
    base.html
    metadata.json
    contract.json
  base_dashboard/
    base.html
    metadata.json
    contract.json
  base_card_grid/
    base.html
    metadata.json
    contract.json
  base_timeline/
    base.html
    metadata.json
    contract.json
  base_profile/
    base.html
    metadata.json
    contract.json
  base_report/
    base.html
    metadata.json
    contract.json
```

Base nen nhe:

- layout skeleton ro rang;
- co slot/regions nhu `hero`, `summary`, `metrics`, `chart_area`, `details`, `footer`;
- co parameters co the set nhu `title`, `theme`, `density`, `show_footer`;
- co hooks de chen component phu, vi du `before_metrics`, `after_chart`, `footer_note`;
- co contract noi ro slot nao nhan component nao;
- CSS tokens co san;
- khong chua qua nhieu component mac dinh;
- khong phu thuoc domain qua manh.

Vi du contract cua base dashboard:

```json
{
  "id": "base_dashboard",
  "slots": {
    "hero": {
      "accepts": ["metric_hero", "summary_hero"],
      "max_components": 1
    },
    "metrics": {
      "accepts": ["metric_card"],
      "max_components": 6
    },
    "chart_area": {
      "accepts": ["line_chart", "bar_chart"],
      "max_components": 1
    },
    "details": {
      "accepts": ["detail_list", "data_table"],
      "max_components": 2
    }
  },
  "parameters": {
    "title": "string",
    "theme": ["light_blue", "neutral", "dark"],
    "density": ["compact", "comfortable"],
    "show_footer": "boolean"
  },
  "hooks": {
    "before_metrics": {
      "accepts": ["alert_banner"]
    },
    "after_chart": {
      "accepts": ["source_note", "summary_text"]
    }
  },
  "missing_field_policy": "hide_component"
}
```

Muc tieu cua base la giup LLM khong phai thiet ke lai toan bo cau truc HTML/CSS tu dau. LLM chi can tao to-do list va/hoac fill plan de lap day cac slot/parameters/hooks theo contract.

### 4.3 Component Library

Component la snippet co the lap ghep vao base.

Vi du:

```text
components/
  metric_card/
    component.html
    metadata.json
  alert_banner/
    component.html
    metadata.json
  forecast_chart/
    component.html
    metadata.json
  data_table/
    component.html
    metadata.json
  source_list/
    component.html
    metadata.json
  timeline_item/
    component.html
    metadata.json
```

Component metadata nen co:

```json
{
  "id": "forecast_chart",
  "visual_type": "chart",
  "required_fields": ["forecast.days[].date", "forecast.days[].temperature"],
  "optional_fields": ["forecast.days[].rain_probability"],
  "supports_domains": ["weather"],
  "needs_javascript": true,
  "external_network": false
}
```

Component library giup template moi duoc tao nhanh hon, nhat quan hon va it loi hon.

### 4.4 Theme Tokens

Nen tach style tokens ra khoi template de Template Agent chi can chon/cau hinh theme thay vi viet CSS moi tu dau.

Vi du:

```json
{
  "theme_id": "light_blue",
  "background": "#f7fbff",
  "surface": "#ffffff",
  "text": "#1f2937",
  "muted": "#6b7280",
  "accent": "#2563eb",
  "danger": "#dc2626",
  "radius": "8px"
}
```

## 5. Metadata la trung tam cua he thong

Moi complete template, base template va component phai co metadata. Metadata dung de search, score, validate va explain vi sao asset duoc chon.

Template metadata de xuat:

```json
{
  "id": "weather_forecast_dashboard",
  "kind": "complete_template",
  "domain": "weather",
  "schema_versions": ["weather.forecast.v1"],
  "visual_type": "dashboard",
  "layout": "dashboard",
  "required_fields": [
    "location",
    "current.temperature",
    "current.condition",
    "forecast.days[].date",
    "forecast.days[].temperature"
  ],
  "optional_fields": [
    "current.humidity",
    "current.wind_speed",
    "forecast.days[].rain_probability"
  ],
  "components": [
    "metric_card",
    "forecast_chart",
    "alert_banner"
  ],
  "style_tags": ["clean", "light", "blue", "responsive"],
  "created_by": "human_or_template_agent",
  "version": "1.0.0"
}
```

Base metadata de xuat:

```json
{
  "id": "base_dashboard",
  "kind": "base_template",
  "layout": "dashboard",
  "regions": ["hero", "summary", "metrics", "chart_area", "details"],
  "best_for": ["metrics", "charts", "multi-section summaries"],
  "style_tags": ["neutral", "responsive"],
  "complexity": "medium"
}
```

Component metadata de xuat:

```json
{
  "id": "metric_card",
  "kind": "component",
  "visual_type": "metric",
  "required_fields": ["label", "value"],
  "supports_domains": ["weather", "finance", "analytics"],
  "needs_javascript": false
}
```

## 6. Luong khi user dung template co san

Day la luong nhanh va nen la default neu co template compatible.

Co 2 truong hop:

```text
1. User da chon template_id cu the
   Vi du: weather_basic
   -> Registry lookup template_id
   -> Renderer render

2. User chua chon template_id
   Vi du: "hien thi thoi tiet dang dep dep"
   -> Registry recommend existing templates bang metadata scoring
   -> Auto mode: chon template score cao nhat neu du threshold
   -> Choose mode: hien 1-3 template cho user pick
   -> Renderer render sau khi co template_id
```

Trong ca 2 truong hop tren, Orchestrator khong can goi LLM va khong can Template Agent neu existing template du phu hop.

```text
1. User hoi domain task
2. Domain Agent tra ve:
   - answer
   - domain
   - structured data
   - schema_version
   - available_fields
3. Neu user da chon template_id:
   - Registry lookup template_id
4. Neu user chua chon template_id:
   - Registry recommend existing templates bang metadata
   - auto chon hoac cho user pick
5. Registry doc metadata/template path
6. Renderer render HTML voi data co field nao thi hien field do
7. Save output file
```

Khong goi Template Agent trong luong nay.

Tools/code can dung:

```text
list_templates(domain)
recommend_templates(domain, schema_version, available_fields, filters)
lookup_template(template_id)
read_template_metadata(template_id)
check_template_compatibility(template_id, data_schema)
render_template(template_id, answer, data)
save_visualization_output(html)
```

Tat ca buoc tren la deterministic, khong can LLM.

## 7. Luong khi user mo ta template mong muon

Day la luong quan trong cho Template Agent.

Vi du user noi:

```text
Tao template thoi tiet dang dashboard, nhiet do lon o giua,
co bieu do 7 ngay, nen xanh nhe, neu co kha nang mua cao thi hien canh bao.
```

Quy trinh de xuat:

```text
1. Orchestrator xac dinh day la create/customize request
2. Orchestrator goi Template Agent workflow va truyen:
   - user_visualization_request
   - domain_result
   - mode/output_target
3. Template Agent workflow tu xu ly:
   - extract requirements tu user prompt
   - inspect actual data
   - search complete/base/component bang metadata
   - filter visible components
   - generate to-do list
   - generate fill plan
   - assemble template tu base contract + components
   - validate
   - render preview
   - repair neu fail
   - save template
   - tra ve template_id/template_html cho Orchestrator
4. Orchestrator goi Renderer deterministic de render final output
```

Neu user chi noi "hien thi dep dep" nhung khong yeu cau tao/sua template, khong nen mac dinh goi Template Agent. Nen uu tien Registry recommend existing templates truoc.

## 8. Extract requirements

Buoc nay nen dung LLM vi can hieu ngon ngu tu nhien cua user.

Input:

```text
user_template_description
domain result metadata
available schema summary
```

Output nen la JSON co cau truc:

```json
{
  "domain": "weather",
  "intent": "create_template",
  "layout": "dashboard",
  "visual_type": "dashboard_with_chart",
  "style": {
    "theme": "light",
    "primary_color": "blue",
    "density": "medium"
  },
  "components": [
    "current_temperature_hero",
    "condition_summary",
    "seven_day_chart",
    "rain_alert"
  ],
  "data_requirements": [
    "location",
    "current.temperature",
    "current.condition",
    "forecast.days[].date",
    "forecast.days[].temperature",
    "forecast.days[].rain_probability"
  ],
  "constraints": {
    "responsive": true,
    "no_external_network": true,
    "allow_internal_js": true
  },
  "confidence": 0.91
}
```

Neu confidence thap hoac yeu cau mo ho, he thong co the hoi lai user. Tuy nhien trong fast mode, co the tiep tuc voi assumption hop ly va tao preview.

## 9. Search template/base/component

Buoc search nen deterministic, dua tren metadata va scoring.

Khong nen:

```text
Agent doc toan bo template.html trong thu vien lon roi tu suy luan.
```

Nen:

```text
Agent/code doc metadata index
Tinh score
Chi doc template/base/component co score cao
```

Scoring goi y cho complete template:

```text
+40 domain match
+30 schema_version match
+20 required fields satisfied
+10 visual_type/layout match
+5 style_tags match
-reject neu thieu required field quan trong
```

Scoring goi y cho base template:

```text
+40 layout match
+20 visual_type match
+20 co regions can thiet
+10 complexity phu hop
+10 style_tags gan dung
```

Scoring goi y cho component:

```text
+40 component type match
+30 required fields satisfied
+20 domain supported
+10 khong can external network
-hidden neu required fields khong co trong available_fields
```

## 10. Generate to-do list

Buoc nay nen dung LLM. To-do list la cau noi giua requirement da extract, data thuc te, base template, component library va template generation.

To-do list khong nen chi dua tren mo ta cua user. No phai dua tren nhieu yeu to de tranh tao template vuot qua du lieu hien co.

Input:

```text
extracted_requirements
actual_data_report
schema_version
available_fields
selected_base_metadata
selected_component_metadata
selected_components
visible_components
hidden_components
guardrails
output_target
```

Y nghia cac input quan trong:

```text
extracted_requirements:
  Yeu cau da extract tu user, vi du layout, style, component mong muon.

actual_data_report:
  Tom tat data thuc te tu sub-agent/tool/API. Vi du weather current, forecast,
  combined, error hoac empty.

schema_version:
  Schema cua data hien tai, vi du weather.current.v1, weather.forecast.v1,
  weather.combined.v1.

available_fields:
  Danh sach field that su co trong data. Template khong nen hien field nam
  ngoai danh sach nay.

selected_base_metadata:
  Base template da duoc chon tu candidate_base_templates bang LLM selection
  co kiem soat. Base nay co contract/regions nhu hero, metrics, chart_area,
  details.

selected_components:
  Component da duoc LLM chon trong visible_components. LLM khong duoc tu bia
  component_id ngoai danh sach visible_components.

visible_components:
  Component du field de hien thi.

hidden_components:
  Component bi an vi data hien tai khong co field can thiet. Danh sach nay
  dung cho log/to-do, khong can hoi lai user trong MVP.

guardrails:
  Security/rendering rules, vi du no external script, no network request,
  conditional rendering cho field co the thieu.

output_target:
  Tao template reusable moi, sua template cu, fork template, hay render mot lan.
```

Policy missing fields trong MVP:

```text
Data co field nao thi template hien field do.
Data thieu field nao thi template khong hien field do.

Khong can:
- phan loai hard/soft/optional fields
- fetch them data
- hoi lai user chi vi thieu field
- render placeholder rong/None/undefined
```

Output:

```json
{
  "base_template": "base_dashboard",
  "selected_components": [
    "metric_card",
    "forecast_chart",
    "alert_banner"
  ],
  "design_tasks": [
    {
      "id": "layout_hero_temperature",
      "instruction": "Use the hero region for current temperature, location and condition."
    },
    {
      "id": "layout_forecast_chart",
      "instruction": "Use chart_area for a 7-day temperature chart."
    },
    {
      "id": "style_light_blue",
      "instruction": "Apply a light theme with blue accent and readable contrast."
    }
  ],
  "technical_tasks": [
    {
      "id": "bind_current_weather",
      "instruction": "Bind only available current weather fields such as location and current.temperature.current_celsius."
    },
    {
      "id": "bind_forecast_days",
      "instruction": "Bind forecast.days[] only when forecast data exists."
    },
    {
      "id": "hide_missing_fields",
      "instruction": "Do not render components whose required fields are not present in available_fields."
    },
    {
      "id": "conditional_rendering",
      "instruction": "Use conditional rendering so missing fields are hidden instead of shown as empty values."
    }
  ]
}
```

To-do list khong nhat thiet phai duoc user confirm trong fast mode. Nhung nen luu vao metadata/log de debug va audit.

## 10.1 Selection policy cho base va components

Khi tao/custom template, Template Agent duoc dung LLM de chon base va components, nhung chi trong danh sach da duoc system loc san.

Quy tac:

```text
1. search_base_templates_by_metadata tra candidate_base_templates top K.
2. decide_template_strategy_with_llm quyet dinh dung base nao va strategy nao:
   - use_base_as_is
   - use_base_with_template_level_adjustments
   - create_new_base_template
3. Neu strategy dung base co san, selected_base.id bat buoc nam trong candidate_base_templates.
4. Neu base chi khop mot phan, agent duoc tao adjustment o cap template/output, khong sua va khong luu lai base reusable moi.
5. Chi khi khong co base phu hop moi chon create_new_base_template.
6. Base moi hoan toan phai la fillable base co contract va duoc luu vao base library.
7. search_components_by_metadata tra candidate_components dua tren requirements va selected_base/new_base contract.
8. filter_visible_components tach visible_components va hidden_components dua tren available_fields.
9. select_components_with_llm chi duoc chon component_id trong visible_components.
10. hidden_components chi dung de log/giai thich, khong duoc dua vao slots/hooks.
11. Neu khong co candidate phu hop va khong duoc tao base/component moi, workflow tra ve no_suitable_base_or_component thay vi de LLM tu bia id.
```

Ly do:

```text
- LLM van co vai tro design planner.
- System van kiem soat duoc toc do va do chinh xac.
- Khong doc ca thu vien template/component.
- Giam hallucination component_id/base_id.
- Dam bao component duoc chon co data de hien thi.
```

## 11. Phuong an chot: Fill base contract bang fill plan

Day la phuong an chinh de trien khai. Buoc nay co the dung LLM, nhung muc tieu khong phai sinh HTML tu dau. Muc tieu la doc base contract, to-do list va component metadata, sau do tao `fill_plan.json`. Code deterministic se assemble fill plan vao base template de tao `template.html`.

Chi nen dua vao prompt nhung thu can thiet:

```text
- base contract da chon
- base skeleton/slots can fill
- component metadata/snippets da chon
- to-do list
- schema summary
- available_fields
- placeholder convention
- security/rendering rules
```

Khong nen dua toan bo thu vien template vao prompt.

Output mong muon:

```text
fill_plan.json
template.html sau khi assemble
metadata.json draft
```

LLM nhan base contract + selected components + to-do list, sau do tra ve fill plan. Code deterministic se assemble fill plan vao base.

Vi du fill plan:

```json
{
  "base_template": "base_dashboard",
  "parameters": {
    "title": "Thoi tiet Ha Noi",
    "theme": "light_blue",
    "density": "comfortable",
    "show_footer": true
  },
  "slots": {
    "hero": ["current_temperature_hero"],
    "metrics": ["humidity_card", "wind_card", "pressure_card"],
    "chart_area": ["forecast_chart"],
    "details": ["forecast_day_list"]
  },
  "hooks": {
    "before_metrics": [],
    "after_chart": ["source_note"]
  },
  "hidden_components": ["uv_badge", "rain_alert"]
}
```

Uu diem:

- khong sinh HTML tu dau;
- nhanh hon khi base/component da tot;
- ket qua nhat quan;
- code assemble co the deterministic;
- de validate slot/parameter/hook contract.

Nhuoc diem:

- can thiet ke contract tot;
- can co assembler doc fill plan va ghep vao base.

Khong trien khai patch theo regions/components trong luong chinh. Neu sau nay can update/repair template phuc tap, co the them patch flow o giai doan sau, nhung MVP chi dung fill plan.

```text
MVP: fill base contract bang fill plan
Later optional: patch/update flow cho template da ton tai neu that su can
```

## 12. Validate template

Validate nen deterministic, khong goi LLM mac dinh.

Validation can co:

```text
validate_html_parse
validate_template_syntax
validate_required_placeholders
validate_schema_compatibility
validate_no_external_network
validate_no_disallowed_tags
validate_no_inline_event_handlers
validate_size_limit
validate_component_requirements
```

Vi du guardrails:

```text
- Khong external script
- Khong iframe
- Khong form submit ra ngoai
- Khong inline event handler nhu onclick/onload
- Neu dung JS thi chi dung internal JS doc JSON injected
- Khong network request
- Gioi han kich thuoc template
- Required fields phai ton tai trong schema
```

LLM chi duoc goi sau validate fail, voi vai tro repair:

```text
validation_error_report
  -> repair_template_with_llm
  -> validate lai
```

Nen gioi han so lan repair, vi du toi da 2 lan.

## 13. Preview template

Preview cung nen deterministic.

Luon co sample data theo schema:

```text
schemas/weather.forecast.v1/sample.json
```

Preview flow:

```text
1. render template voi sample_data
2. save preview HTML
3. optional: mo headless browser chup screenshot
4. optional: kiem tra output khong blank
```

LLM visual review chi nen la optional quality mode:

```text
preview screenshot + original requirement
  -> LLM nhan xet template co dung yeu cau thiet ke khong
```

Khong nen bat buoc trong MVP vi cham va ton token.

## 14. Template Agent co chu trinh gi?

Template Agent nen la workflow agent co cac node ro rang, khong phai agent tu do hoan toan.

Chu trinh de xuat:

```text
START
  |
  v
extract_requirements_with_llm
  |
  v
load_schema_summary
  |
  v
inspect_actual_data
  |
  v
search_complete_templates_by_metadata
  |
  +-- complete template high score?
  |       |
  |       +-- yes:
  |             recommend/use/fork
  |             validate
  |             return template artifact
  |             END
  |
  v
search_base_templates_by_metadata
  |
  v
decide_template_strategy_with_llm
  |
  +-- use_base_as_is:
  |       selected_base = candidate base
  |
  +-- use_base_with_template_level_adjustments:
  |       selected_base = candidate base
  |       adjustments are applied only to template/output, not saved as reusable base
  |
  +-- create_new_base_template:
          generate_new_base_with_llm
          validate_base_contract
          save_new_base_to_library
          selected_base = new base
  |
  v
search_components_by_metadata
  |
  v
filter_visible_components
  |
  v
select_components_with_llm
  |
  v
generate_todo_list_with_llm
  |
  v
generate_fill_plan_with_llm
  |
  v
assemble_template_from_base
  |
  v
deterministic_validate
  |
  +-- valid?
  |       |
  |       +-- yes:
  |             render_preview
  |             save_template
  |             return template artifact
  |             END
  |
  v
repair_template_with_llm
  |
  v
validate_again
  |
  +-- valid -> save/return template artifact
  +-- invalid after max retries -> report failure with error details
```

## 15. Template Agent co dung tool calling khong?

Co, nhung nen phan biet:

```text
Template Agent = workflow agent co kiem soat
LLM calls = mot so node trong workflow
Tools = ham deterministic hoac file/schema/template operations
```

Khong nen moi tool deu la LLM. Tools quan ly file, search, validate, render, save nen la deterministic.

### 15.1 LLM steps

Nhung buoc nen call LLM:

```text
extract_requirements(user_prompt, domain_context, schema_summary)
decide_template_strategy(
  requirements,
  candidate_complete_templates,
  candidate_base_templates,
  actual_data_report,
  metadata_scores,
  rules
)
generate_new_base(requirements, actual_data_report, rules)
select_components(requirements, selected_base, visible_components, hidden_components, rules)
generate_todo_list(
  requirements,
  actual_data_report,
  selected_base,
  selected_components,
  hidden_components,
  schema_version,
  available_fields,
  rules
)
generate_fill_plan(base_contract, selected_components, todo_list, available_fields, rules)
repair_template(template_html, validation_errors, schema_summary, rules)
optional_visual_review(preview_screenshot, original_requirements)
```

`optional_visual_review` khong nen bat buoc trong MVP.

### 15.2 Deterministic tools

Nhung tool khong nen call LLM:

```text
list_templates(domain)
search_templates(filters)
read_template(template_id)
read_template_metadata(template_id)

list_base_templates()
search_base_templates(filters)
read_base_template(base_id)
read_base_metadata(base_id)
read_base_contract(base_id)
validate_base_contract(base_contract)
save_base_template(base_id, base_html, base_contract, metadata)

list_components(filters)
search_components(filters)
read_component(component_id)
read_component_metadata(component_id)

get_schema(domain, schema_version)
get_schema_summary(domain, schema_version)
get_sample_data(domain, schema_version)

inspect_actual_data(agent_data)
filter_visible_components(available_fields, candidate_components)
assemble_template_from_base(base_template, base_contract, fill_plan, components)

check_compatibility(asset_metadata, schema)
validate_template_syntax(template_html)
validate_placeholders(template_html, schema)
validate_security(template_html)
sanitize_template(template_html)

render_template(template_html, answer, data)
render_preview(template_html, sample_data)
save_template(template_id, template_html, metadata)
save_visualization_output(html)
```

Trong MVP, `inspect_actual_data` va `filter_visible_components` nen don gian:

```text
inspect_actual_data:
  - doc output that tu sub-agent, vi du weather_data
  - lay domain, schema_version, data_type
  - lay available_fields
  - khong dung answer text lam nguon data chinh

filter_visible_components:
  - component nao du field thi dua vao visible_components
  - component nao thieu field thi dua vao hidden_components
  - khong phan loai hard/soft/optional
  - khong fetch them data
  - khong hoi lai user
```

LLM decision/selection steps chi duoc quyet dinh trong pham vi da loc:

```text
decide_template_strategy_with_llm:
  - input: candidate_complete_templates, candidate_base_templates, metadata_scores
  - output: strategy + selected_template/selected_base/new_base_request
  - neu strategy dung template/base co san, id bat buoc nam trong candidate list
  - neu base khop mot phan, chi duoc de xuat template-level adjustments
  - khong duoc sua base goc va khong luu base reusable moi trong truong hop nay
  - chi chon create_new_base_template khi khong co base phu hop
  - base moi phai co contract fillable va metadata de luu vao base library

llm_generate_new_base:
  - chi duoc goi khi strategy = create_new_base_template
  - output gom base.html, contract.json, metadata.json
  - base moi phai co slots, parameters, hooks va missing_field_policy
  - base moi duoc validate va save vao base library

select_components_with_llm:
  - input: visible_components, hidden_components
  - output: selected_components
  - moi selected component bat buoc nam trong visible_components
  - khong duoc dua hidden_components vao slots/hooks
  - khong duoc tu bia component_id
```

### 15.3 Tools co the goi LLM ben trong?

Co the co wrapper tools kieu:

```text
llm_extract_requirements
llm_decide_template_strategy
llm_generate_new_base
llm_select_components
llm_generate_todo_list
llm_generate_fill_plan
llm_repair_template
```

Nhung nen dat ten ro de biet tool nao ton token va co latency.

De xuat chia tool thanh 2 nhom:

```text
deterministic_tools/
llm_tools/
```

## 16. Phuong an chot: workflow co dinh, LLM o mot so node

Template Agent nen duoc trien khai theo workflow co dinh. LLM khong tu do goi tool lung tung. Workflow code dieu phoi thu tu cac buoc, con LLM chi duoc dung o cac node can suy luan.

```text
Workflow code:
  requirements = llm_extract(...)
  base_candidates = search_base_by_metadata(...)
  strategy = llm_decide_template_strategy(base_candidates, requirements)
  selected_base = resolve_or_create_base_from_strategy(strategy)
  component_candidates = search_components_by_metadata(...)
  visible_components = filter_visible_components(...)
  selected_components = llm_select_components(visible_components)
  todo = llm_plan(selected_base, selected_components)
  fill_plan = llm_generate_fill_plan(...)
  template = assemble_template_from_base(...)
  validation = validate(...)
  if fail: template = llm_repair(...)
```

Uu diem:

- on dinh hon;
- de debug;
- de retry;
- de do latency/token;
- validate/save luon theo thu tu bat buoc.

Nhuoc diem:

- it linh hoat hon agent tu do;
- can thiet ke workflow ro tu dau.

Day la phuong an chot cho MVP vi phu hop muc tieu toc do nhanh va do chinh xac cao.

```text
Chot:
  Dung workflow co dinh.
  LLM chi nam o cac node:
    - extract_requirements
    - decide_template_strategy
    - select_components
    - generate_todo_list
    - generate_fill_plan
    - repair_template neu validate fail

  Khong dung open-ended autonomous tool-calling agent trong MVP.
```

Neu sau nay can task rat phuc tap nhu refactor template lon hoac multi-step repair kho, co the them che do agent tu do co gioi han. Nhung do khong phai luong chinh.

## 17. Nen tao moi template hay fork/customize?

Template Agent nen co quyen quyet dinh strategy, nhung phai dua tren candidate templates/base/components, actual data, metadata scores va user requirements da duoc truyen vao.

Strategy de xuat:

```text
1. use_existing_template
   Dung complete template co san neu compatible va match yeu cau.

2. fork_or_customize_existing_template
   Dung khi complete template gan dung nhung can sua nho.

3. use_base_as_is
   Dung base template phu hop va fill slots/parameters/hooks theo contract.

4. use_base_with_template_level_adjustments
   Dung khi base khop mot phan va can chinh nho.
   Chinh o cap template/output, khong sua base goc, khong luu base reusable moi.

5. create_new_base_template
   Chi dung khi khong co base nao phu hop voi layout/interaction user muon.
   Base moi phai la fillable base co contract va metadata.
   Base moi duoc luu vao base library de tai su dung.

6. one_off_blank_fallback
   Fallback cuoi cung, nen tranh trong MVP va chi dung neu user cho phep.
```

Policy quan trong:

```text
Co base phu hop ma can sua:
  agent chon base do, tao template-level adjustments, assemble output/template.
  Khong luu lai thanh base reusable moi.

Khong co base phu hop:
  agent co the chon create_new_base_template.
  Base moi phai fillable, co slots/parameters/hooks contract, va luu vao base library.
```

Khong nen tao tu blank mac dinh vi:

- cham hon;
- nhieu loi hon;
- style kem nhat quan;
- validate kho hon.

## 18. UX de xuat

Nen ho tro 3 mode:

### Auto mode

System tu chon template tot nhat bang metadata score.

```text
User hoi -> co data -> auto render bang template phu hop nhat
```

### Choose mode

System hien 1-3 template/base options cho user chon.

```text
1. Weather basic card
2. Forecast dashboard
3. Tao template moi theo mo ta
```

### Create/customize mode

User mo ta template moi hoac yeu cau sua template.

```text
User: Tao dashboard co chart 7 ngay va canh bao mua
Template Agent: extract -> base/component -> todo -> fill plan -> assemble -> validate -> preview -> save template
Orchestrator: nhan template artifact -> Renderer render final output
```

## 19. Metadata luu khi template duoc tao

Template do agent tao nen luu metadata day du:

```json
{
  "id": "weather_dashboard_rain_alert",
  "domain": "weather",
  "schema_versions": ["weather.forecast.v1"],
  "created_by": "template_agent",
  "created_at": "2026-07-11T00:00:00+07:00",
  "source": {
    "base_template": "base_dashboard",
    "base_contract": "base_dashboard/contract.json",
    "components": ["metric_card", "forecast_chart", "alert_banner"],
    "original_user_prompt": "Tao template thoi tiet dang dashboard..."
  },
  "extracted_requirements": {},
  "todo_list": {},
  "fill_plan": {},
  "validation": {
    "status": "passed",
    "validated_at": "2026-07-11T00:00:00+07:00"
  },
  "version": "1.0.0"
}
```

Metadata nay giup:

- search lan sau nhanh hon;
- explain template duoc tao nhu the nao;
- fork/sua template de hon;
- audit LLM output;
- rollback/versioning.

## 20. Giai doan trien khai

### Giai doan 1: MVP deterministic render

Muc tieu: chung minh `answer + structured data + template -> html`.

Lam:

```text
- Tao templates/weather/weather_basic
- Tao metadata.json cho template
- Tao Visualization Orchestrator mong: route lookup/recommend/render
- Tao renderer deterministic
- Tao compatibility checker don gian
- Cho user chon template co san
```

Chua can Template Agent.

### Giai doan 2: Metadata search + base nhe

Muc tieu: co nen tang de chon template/base nhanh.

Lam:

```text
- Tao base_templates/
- Tao components/
- Tao contract.json cho base templates
- Tao metadata index
- Them lookup/recommend/search/scoring deterministic trong Registry
- Them sample data theo schema
```

Van co the chua can sinh template bang LLM.

### Giai doan 3: Template Agent workflow

Muc tieu: user co the mo ta template moi.

Lam:

```text
- llm_extract_requirements
- inspect_actual_data
- search base/component bang metadata
- llm_decide_template_strategy
- optional llm_generate_new_base
- filter_visible_components
- llm_select_components
- llm_generate_todo_list
- llm_generate_fill_plan
- deterministic assemble_template_from_base
- deterministic validate
- deterministic preview
- save template
```

### Giai doan 4: Repair va customization

Muc tieu: template agent co the sua loi va sua template cu.

Lam:

```text
- llm_repair_template khi validate fail
- fork_template
- update_template
- versioning
- preview before save
```

### Giai doan 5: Visual QA nang cao

Muc tieu: nang chat luong giao dien.

Lam:

```text
- headless browser screenshot
- blank/overflow check
- optional llm_visual_review
- compare preview voi original requirement
```

## 21. Ket luan thiet ke

Huong nen chot:

```text
Complete template co san:
  Orchestrator lookup/recommend path
  deterministic registry + compatibility + renderer
  khong goi LLM

User muon template moi/custom:
  Orchestrator chi route sang Template Agent
  Template Agent workflow
  LLM extract requirement
  deterministic metadata search
  LLM generate to-do list
  LLM generate fill plan tu base contract + visible components
  deterministic assemble template
  deterministic validate + preview
  LLM repair chi khi fail
  deterministic save template
  Orchestrator goi Renderer de render final output
```

Visualization Orchestrator nen mong: chi chon path lookup/recommend/render/create/customize. Moi logic cu the ve base/component/slot/to-do/fill plan nam trong Template Agent workflow va deterministic tools cua no.

Base template nen nhe, component library nen co metadata ro, va template selection nen dua tren metadata scoring. Cach nay giup nhanh hon sinh tu dau, giam loi, giu UI nhat quan va tranh viec agent phai doc mot thu vien template lon moi lan render.

## 22. Kien truc hoi thoai Template Agent - phien ban cap nhat

Template Agent phai la workflow co trang thai, khong phai mot request stateless tao artifact ngay lap tuc.

```text
user input
  -> Requirement Agent (LLM 1: gate + extract requirements)
  -> needs_clarification: hoi lai, luu pending_template_state, dung luot
  -> ready: inspect actual data
  -> registry tools: tim complete/base/component theo domain + schema + requirements
  -> Strategy Agent (LLM 2: strategy + component selection + todo list)
  -> deterministic fill plan/assemble/validate
  -> preview + cho user xac nhan/chinh sua
```

Requirement JSON la intermediate UI Requirements Specification, khong chua HTML,
CSS hay template_id. No gom purpose, content priority, presentation, style,
constraints, status, missing_information va clarifying_question. Cau hoi danh cho
nguoi dung phai dung ngon ngu doi thuong, khong bat buoc biet dashboard/layout/component.

`inspect_actual_data` la buoc deterministic doc domain, schema_version va
available_fields tu domain result. Domain duoc dung de gioi han compatibility;
base template/component co the tai su dung xuyen domain neu contract phu hop.

Chi dung hai lan goi LLM trong luong chinh: LLM 1 gate/extract va LLM 2 strategy/
components/todo. Registry, compatibility, fill-plan assembly, validation, render
va save phai la tools/code deterministic.

## 23. Luong thay doi template va lua chon tao moi

Yeu cau "doi template" duoc coi la `show_template_options`, khong can LLM phan
loai ngay thanh chon mau cu hay tao mau moi. Danh sach compatible templates luon
co them mot action pseudo-item `__create_new_template__`.

```text
doi template
  -> LLM1/action understanding
  -> list_compatible_templates(domain, schema, fields)
  -> hien templates + Tao template moi
  -> user chon tu nhien, co the kem modification
```

LLM1 phan tich ca selection va modification. Vi du "mau 2, doi nen hong" phai
tao ra selection index=2 va modification style.background_color=pink. Code phai
validate index/template_id va chi ap dung modification qua design tokens,
component slots hoac fill plan; LLM khong sua HTML truc tiep.

Neu user chon `__create_new_template__`, chuyen sang requirement gate va hoi mo ta
bang ngon ngu doi thuong. Neu user noi ro "tao template moi" ngay tu dau thi co
the bo qua buoc list va vao requirement gate truc tiep.

LLM1 la source of truth cho semantic interpretation cua template requests. Parser
code chi nen la fallback offline/optimization, khong duoc la noi quyet dinh chinh
cho cac cau tu nhien. LLM1 output phai duoc validate truoc khi code goi registry,
Template Agent hay renderer.

Neu request la `create`/`customize` va co user description, viec tim thay
complete_template compatible khong duoc ket thuc workflow bang
`Existing template match selected.`. Complete template chi la candidate/source;
workflow phai tiep tuc assemble/customize, ap dung allow-listed design tokens,
render preview va save artifact.
