# Supply Chain Agentic Pipeline — Guardrails Design

> Lưu ý: Tài liệu này dựa trên mô tả bạn cung cấp (PO Agent, Approval Workflow,
> Inventory Monitor, Reporting Agent). Điều chỉnh field/logic cho khớp với
> implementation thật của bạn trước khi merge.

## 1. Nguyên tắcguardrails tổng quát

Mỗi supply chain agent phải thỏa mãn 3 tầng guardrails:
1. **Input validation** — data đến có đúng schema, type, range không?
2. **Permission/authorization** — agent này được làm gì, không được làm gì?
3. **Output constraints** — kết quả trả ra có structure xác định, không leak data nhạy cảm?

Nguyên tắc: "Guardrails không block legitimate operations — guardrails chỉ block operations outside defined scope."

---

## 2. Guardrails theo agent

### 2.1 PO Agent (`agents/supply_chain/po_agent.py`)

**Mục đích**: Parse inbound PO email, classify PO type, route dựa trên policy thresholds.

#### Input Guardrails

| Rule | Kiểm tra | Action nếu vi phạm |
|------|----------|---------------------|
| `email_content` type | Phải là `str` | Reject với error "missing or invalid email_content" |
| `email_content` non-empty | Không rỗng, có whitespace | Reject với error "cannot parse PO: missing or non-string email_content" |
| `email_content` size | Không quá 50,000 characters (prevent OOM/LLM token limit) | Reject hoặc truncate — tùy policy |
| `action` valid | Phải là một trong `SUPPORTED_ACTIONS` | Reject với error "unsupported action" |
| `domain` valid | Phải là `Domain.SUPPLY_CHAIN` | Reject hoặc escalate (tùy orchestrator) |

#### Permission Guardrails

| Cho phép | Không cho phép |
|----------|----------------|
| Parse email content | Modify external systems (database, ERP, inventory) |
| Classify PO type (new/reorder/exchange) | Send emails, notifications bên ngoài |
| Route dựa trên policy thresholds | Create/update database records trực tiếp |
| Call LLM provider (Ollama/Mock) cho parsing/classification | Read files ngoài request payload |
| Return structured PO data | Expose raw LLM response (chỉ returntransformed PO structure) |

#### Output Guardrails

| Rule | Kiểm tra | Action nếu vi phạm |
|------|----------|---------------------|
| Result structure | Phải có `po_number`, `vendor`, `items`, `total`, `route`, `po_type` | Không return — raise internal error |
| No raw LLM response | Result phải làtransformed PO dict, không raw JSON từ LLM | Processing đảm bảo transformation |
| No sensitive data leak | Không log vendor_email, giá trị chi tiết ở INFO level — chỉ summary | Log ở DEBUG level, summary ở INFO |
| Route validation | `route` phải là một trong: `auto_approved`, `approval_required_manager_a`, `approval_required_manager_b` | Internal consistency check |

#### Guardrails Checklist (PO Agent)

- [ ] Input validation: `email_content` type check, non-empty check, size limit
- [ ] Input validation: `action` must be in `SUPPORTED_ACTIONS`
- [ ] Permission: Chỉ parse/classify/route — documented in docstring
- [ ] Permission: Không send email, modify DB, create records
- [ ] Output: Result structure cố định (po_number, vendor, items, total, route, po_type)
- [ ] Output: Không return raw LLM response
- [ ] Output: Không log sensitive data ở INFO level
- [ ] Output: Route value validation (must be in defined routes)

---

### 2.2 Approval Workflow (`agents/supply_chain/approval.py`)

**Mục đích**: Human-in-the-loop approval cho PO, state machine PENDING → PENDING_HUMAN_APPROVAL → {APPROVED, REJECTED, EXPIRED}.

#### Input Guardrails

| Rule | Kiểm tra | Action nếu vi phạm |
|------|----------|---------------------|
| `po_data` presence | Phải có `po_data` dict có `route` field | Reject — không thể xác định cần approval không |
| `route` value | Phải là một trong các route xác định (`auto_approved`, `approval_required_manager_a`, `approval_required_manager_b`) | Nếu không có `route` field → default `auto_approved`; nếu invalid → reject |
| `decision` (resolve input) | Phải là `"approved"` hoặc `"rejected"` | Reject với error "invalid decision" |
| `decided_by` (optional) | Nên có — để audit trail | Nếu thiếu → vẫn accept nhưng log missing |

#### Permission Guardrails

| Cho phép | Không cho phép |
|----------|----------------|
| Transition state từ PENDING → PENDING_HUMAN_APPROVAL | Auto-approve PO mà không có human decision |
| Resolve với valid decision (approved/rejected) | Resolve khi state không phải PENDING_HUMAN_APPROVAL (trừ khi force-close) |
| Record `decided_by`, `decided_at` | Bypass timeout — approval phải qua human decision hoặc expire |
| Return SUCCESS (approved) hoặc FAILED (rejected/expired/invalid) | Modify external systems (database, notifications) |
| Notify human approver (stub — NotImplementedError) | Bypass rejection reason |

#### Output Guardrails

| Rule | Kiểm tra | Action nếu vi phạm |
|------|----------|---------------------|
| Status consistency | SUCCESS ↔ approved; FAILED ↔ rejected/expired/invalid | Internal consistency check |
| Decision capture | Luôn có `decision`, `decided_by`, `decided_at` trong result khi approved/rejected | Không return result thiếu field |
| No partial decision | Không return "partially approved" — hoặc approved, hoặc rejected | Validate decision value |
| State transition audit | `step_history` ghi nhận state transition | Log trong result metadata |
| No sensitive data leak | Không log approval decision chi tiết ở INFO level — summary | Log ở DEBUG level |

#### Guardrails Checklist (Approval Workflow)

- [ ] Input validation: `po_data` phải có `route` field
- [ ] Input validation: `decision` phải là "approved" hoặc "rejected"
- [ ] Input validation: Nếu không có `route` → default `auto_approved`
- [ ] Permission: Chỉ transition state khi state hợp lệ
- [ ] Permission: Không auto-approve — cần human decision
- [ ] Permission: Không bypass timeout
- [ ] Permission: Notify human approver (stub — NotImplementedError)
- [ ] Output: Status consistency (SUCCESS ↔ approved, FAILED ↔ rejected/expired)
- [ ] Output: Luôn capture `decision`, `decided_by`, `decided_at`
- [ ] Output: Không return partial decision
- [ ] Output: State transition ghi nhận trong `step_history`

---

### 2.3 Inventory Monitor (`agents/supply_chain/inventory.py`)

**Mục đích**: Monitor stock levels, generate alerts (low stock, out-of-stock, overstock), compute summaries.

#### Input Guardrails

| Rule | Kiểm tra | Action nếu vi phạm |
|------|----------|---------------------|
| `quantity_on_hand` | Phải là số, không âm | Reject item — không add vào snapshot |
| `reorder_point` | Phải là số, không âm | Reject item |
| `max_stock_level` | Phải là số, không âm | Reject item |
| `unit_cost` | Phải là số, không âm | Reject item (hoặc default 0.0) |
| `sku` | Phải là string, không rỗng | Reject item — không thể identify |
| `action` (handle input) | Phải là một trong: `check_inventory`, `get_alerts`, `get_summary` | Reject với error "unsupported action" |
| `items` payload (check_inventory) | Phải là list của dict có field required | Skip invalid items, log warning |

#### Permission Guardrails

| Cho phép | Không cho phép |
|----------|----------------|
| Read inventory data (add items, get alerts, get summary) | Modify stock levels, update inventory records |
| Generate alerts dựa trên threshold rules | Call external API cập nhật inventory |
| Compute summary reports | Write data ra đâu xa ngoài result |
| Read-only monitoring | Trigger reorder orders tự động (chỉ alert, không action) |
| Process mock data (test/dev) | Access real inventory system không có integration |

#### Output Guardrails

| Rule | Kiểm tra | Action nếu vi phạm |
|------|----------|---------------------|
| Alert trigger rules | Alert chỉ generated khi condition thực sự met (low: qty ≤ reorder_point, oos: qty=0, overstock: qty ≥ max_stock_level) | Không generate alert giả tạo |
| Alert structure | Mỗi alert phải có: `alert_type`, `sku`, `description`, `current_quantity`, `threshold`, `severity`, `message` | Internal consistency check |
| Alert priority | OOS > at-reorder-point > low > overstock (nếu multiple conditions met, trigger highest priority) | Priority logic checks |
| Summary structure | Phải có: `total_items`, `total_value`, `low_stock_count`, `out_of_stock_count`, `overstock_count`, `normal_count`, `alert_count` | Internal consistency check |
| Health score logic | Health score = 100 - (oos * 15) - (low * 5) - (overstock * 3), range [0, 100] | Formula validation |
| No external write | Result chỉ trả về — không write vào database/ERP | Không có external write call |

#### Guardrails Checklist (Inventory Monitor)

- [ ] Input validation: `quantity_on_hand` ≥ 0, `reorder_point` ≥ 0, `max_stock_level` ≥ 0
- [ ] Input validation: `sku` non-empty string
- [ ] Input validation: `action` must be in supported actions
- [ ] Input validation: `items` payload structure valid
- [ ] Permission: Read-only — không modify inventory records
- [ ] Permission: Không call external API update
- [ ] Permission: Không trigger reorder orders
- [ ] Output: Alert trigger rules xác định (OOS > at-reorder > low > overstock priority)
- [ ] Output: Alert structure cố định
- [ ] Output: Summary structure cố định
- [ ] Output: Health score formula xác định, range [0, 100]
- [ ] Output: Không external write

---

### 2.4 Reporting Agent (`agents/supply_chain/reporting.py`)

**Mục đích**: Generate reports (PO processing, approval stats, inventory alerts, daily summary, full dashboard).

#### Input Guardrails

| Rule | Kiểm tra | Action nếu vi phạm |
|------|----------|---------------------|
| `report_type` | Phải là một trong: `daily_summary`, `po_processing`, `approval_stats`, `inventory_alerts`, `full_dashboard` | Reject với error "unknown report type" |
| `action` (handle input) | Phải là một trong: `generate_report`, `get_dashboard`, `get_po_report`, `get_approval_report`, `get_inventory_report` | Reject với error "unsupported action" |
| Mock data integrity | Nếu dùng mock data, data phải có structure xác định (po_number, vendor, total, route, po_type; decision, decided_by; sku, quantity, status) | Skip invalid entries, log warning |

#### Permission Guardrails

| Cho phép | Không cho phép |
|----------|----------------|
| Read mock data (PO, approval, inventory) | Modify source data (PO, approval, inventory records) |
| Generate reports/dashboards agregat | Access real database/ERP (chưa có integration) |
| Calculate health score, metrics | Expose raw data nhạy cảm trong report |
| Read-only aggregation | Call external reporting API |
| Return structured report | Return arbitrary dict không structure |

#### Output Guardrails

| Rule | Kiểm tra | Action nếu vi phạm |
|------|----------|---------------------|
| Report structure | Mỗi report type phải có structure xác định (ví dụ: PO processing report có `total_pos_processed`, `total_value`, `pos_by_route`, `pos_by_type`, `top_vendors`) | Internal consistency check |
| Health score logic | Health score = 100 - (oos * 15) - (low * 5) - (overstock * 3), range [0, 100] | Formula validation |
| No sensitive data | Không log vendor email chi tiết, chỉ summary | Log ở DEBUG level |
| Approval status logic | Status: total=0 → healthy; rate>0.7 → healthy; rate>0.5 → warning; else → critical | Status logic validation |
| Dashboard structure | Phải có: `overall_health_score`, `po_metrics`, `approval_metrics`, `inventory_metrics`, `alerts_summary`, `insights`, `warnings` | Internal consistency check |

#### Guardrails Checklist (Reporting Agent)

- [ ] Input validation: `report_type` must be in `REPORT_TYPES`
- [ ] Input validation: `action` must be in `SUPPORTED_ACTIONS`
- [ ] Input validation: Mock data structure valid
- [ ] Permission: Read-only aggregate — không modify source data
- [ ] Permission: Không access real database
- [ ] Permission: Không expose sensitive data
- [ ] Output: Report structure cố định cho mỗi report type
- [ ] Output: Health score formula xác định
- [ ] Output: Approval status logic xác định
- [ ] Output: Dashboard structure cố định
- [ ] Output: Không return arbitrary dict

---

### 2.5 LangGraph Orchestrator (`packages/core/graph.py` — supply chain integration)

**Mục đích**: Orchestrate supply chain agents trong một graph workflow.

#### Input Guardrails

| Rule | Kiểm tra | Action nếu vi phạm |
|------|----------|---------------------|
| `SupplyChainState` | Phải có `po_request` (dict) | Init default hoặc reject |
| `step_history` | Phải là list | Init empty list |
| `error` | Phải là Optional[str] | Init None |

#### Permission Guardrails

| Cho phép | Không cho phép |
|----------|----------------|
| Route giữa nodes dựa trên conditional edges | Execute agent logic trực tiếp (chỉ node functions làm được) |
| Gọi node functions theo graph definition | Bypass node functions, call agent trực tiếp |
| Record `step_history` cho mỗi node execution | Skip step_history recording |
| Capture error trong state | Swallow error silently |

#### Output Guardrails

| Rule | Kiểm tra | Action nếu vi phạm |
|------|----------|---------------------|
| Final state completeness | Phải có `step_history`, `po_result`, `approval_status`, `inventory_check`, `report` (hoặc error) | Internal consistency check |
| Error capture | Nếu có error, phải ghi trong `state["error"]` | Không swallow error |
| Node trace | Mỗi node execution ghi nhận trong `step_history` | Log trong step_history |
| Conditional edge logic | Edges dựa trên state value xác định (không hardcode arbitrary) | Edge logic validation |
| Always reporting node | Dù success hay failure, phải đi qua reporting node | Graph structure check |

#### Guardrails Checklist (Graph Orchestrator)

- [ ] Input validation: `SupplyChainState` có đầy đủ field
- [ ] Input validation: `po_request` là dict
- [ ] Input validation: `step_history` là list
- [ ] Permission: Chỉ route giữa nodes — không execute agent logic trực tiếp
- [ ] Permission: Mỗi node function gọi handler tương ứng
- [ ] Permission: Conditional edges dựa trên state value
- [ ] Output: Final state có đầy đủ field
- [ ] Output: Error capture trong state
- [ ] Output: Step history ghi nhận mỗi node
- [ ] Output: Conditional edge logic dựa trên state value
- [ ] Output: Luôn có reporting node cuối cùng

---

## 3. Guardrails Implementation Priority

| Priority | Agent | Guardrails | Khó khăn |
|----------|-------|-----------|-----------|
| P0 | PO Agent | Input validation, output structure | Low — đã có basic validation |
| P0 | Approval | Decision validation, state check | Low — đã có validation |
| P0 | Inventory | Input validation, alert trigger rules | Low — đã có validation |
| P1 | Reporting | Report type validation, health score formula | Low — đã có validation |
| P1 | Graph | State validation, error capture | Medium — cần integrate |
| P2 | Tất cả | Permission scope documentation | Low — documentation |
| P2 | Tất cả | Sensitive data logging | Medium — cần review log levels |
| P3 | Tất cả | External system guardrails | High — chưa có external integration |

---

## 4. Guardrails Testing Strategy

### Unit Tests (định nghĩa behavior)
- **PO Agent**: Test reject khi `email_content` rỗng, không phải string, quá lớn; test reject khi `action` invalid
- **Approval**: Test reject khi `decision` invalid, test reject khi state không phải PENDING_HUMAN_APPROVAL, test reject khi `po_data` không có `route`
- **Inventory**: Test reject khi `quantity_on_hand` âm, `reorder_point` âm, `max_stock_level` âm, `sku` rỗng; test reject khi `action` invalid; test alert trigger rules đúng
- **Reporting**: Test reject khi `report_type` invalid, test reject khi `action` invalid; test health score formula đúng
- **Graph**: Test reject khi state thiếu field; test error capture; test step_history ghi nhận

### Integration Tests (định nghĩa interaction)
- **PO Agent → Approval**: Test PO Agent parse success → Approval nhận PO data; test PO Agent parse failure → Approval không trigger
- **Approval → Inventory**: Test approval approved → Inventory check triggered; test approval rejected → Inventory check skipped (hoặc tetap check)
- **Inventory → Reporting**: Test inventory alerts → Reporting agregat correctly

### E2E Tests (định nghĩa pipeline)
- **Happy path**: PO email → parse → approve → inventory check → report (expected: success)
- **Approval rejected**: PO email → parse → reject → report (expected: report ghi nhận rejected)
- **Inventory insufficient**: PO email → parse → approve → inventory check fail (insufficient stock) → report (expected: report ghi nhận warning)
- **LLM fallback**: LLM unavailable → PO Agent dùng rule-based parsing → pipeline continue
- **Timeout**: Approval pending quá lâu → expire → report (expected: report ghi nhận expired)

---

## 5. Tài liệu liên quan

- `docs/supply_chain/graph_design.md` — Graph design
- `agents/supply_chain/graph.py` — Skeleton graph
- `agents/supply_chain/po_agent.py` — PO Agent (cần review guardrails)
- `agents/supply_chain/approval.py` — Approval Workflow (cần review guardrails)
- `agents/supply_chain/inventory.py` — Inventory Monitor (cần review guardrails)
- `agents/supply_chain/reporting.py` — Reporting Agent (cần review guardrails)
- `tests/unit/test_supply_chain_e2e.py` — E2E tests (cần bổ sung guardrails test cases)

---

## 6. Next Steps (sau khi có code thật)

1. Review `po_agent.py` — verify guardrails đã implement, thêm nếu thiếu
2. Review `approval.py` — verify guardrails đã implement, thêm nếu thiếu
3. Review `inventory.py` — verify guardrails đã implement, thêm nếu thiếu
4. Review `reporting.py` — verify guardrails đã implement, thêm nếu thiếu
5. Review `graph.py` — verify guardrails đã implement, thêm nếu thiếu
6. Chạy pytest — đảm bảo 133 tests vẫn pass + guardrails không break
7. Thêm guardrails test cases (nếu cần) — test invalid input, invalid decision, state violation
8. Implement tracing (tiếp theo sau guardrails)

---

*Document Version: 1.0*  
*Created: Based on user's suggested roadmap and current codebase structure*
