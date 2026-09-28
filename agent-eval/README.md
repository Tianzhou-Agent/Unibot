# Unibot agent eval

A black-box test project for the Unibot agent. It runs scripted conversations against the backend's REST API,
reads the trace the backend records from its OpenTelemetry spans, and scores each run on two things:

* **Behaviour**: tool selection, tool arguments, context management, isolation, approvals and safety.
* **Performance**: latency, time to first token, tokens, iterations and redundant calls.

Each case runs **k** times. The report gives both **pass@k** (the case passed at least once) and **pass^k**
(the case passed every time). pass^k is closer to what users actually experience.

This project sits alongside the deterministic tests and the DeepEval suite in `backend/tests`. It doesn't
replace them. It doesn't import backend code, so the same suite can run against a local, staging or
production deployment.

```
datasets/*.yaml ──► runner ──► POST /chat/stream (or /chat)     ◄─ traceparent header (W3C)
                      │        GET  /traces/{trace_id}          ◄─ events + OTel spans of the run
                      │        GET  /llm-calls                  ◄─ exact messages the model received
                      │        POST /approvals/{id}/confirm|deny, DELETE /conversations/{id}
                      ▼
          deterministic checks ─► LangChain LLM judge (optional) ─► report.md / report.json
                      │
                      └─► OTel spans: eval.run › eval.case › eval.attempt › eval.turn (+ eval.check events)
```

## What gets checked

| Category | Checks | Data source |
|---|---|---|
| `tool_selection` | Trajectory match in `strict`, `unordered`, `superset` or `subset` mode (F1 score); `none`; `forbidden` tools; no failed calls | `tool.*`, `aina.*` and `builtin.*` trace events, merged with tool spans |
| `tool_arguments` | Arguments are a subset or exact match, case-insensitive, with `re:` regex values | `details.arguments` / span `input` |
| `context` | The prompt contains, or doesn't contain, given facts; a compaction summary is present; message count stays under a limit | `GET /llm-calls`: what the model actually saw |
| `output` | `equals`, `contains`, `contains_any`, `not_contains`, `regex` | Response `content` |
| `protocol` | Status (`completed`, `approval_required`, `failed`), expected backend errors (`error`), widgets (`app_list`, `navigation`, `form` ...), trace events such as `approval.denied` and `context.compacted`, API setup steps (`http`) and background-job results (`poll`) | Response + trace + API |
| `llm_calls` | The model that actually served each agent call (`served_model`, as reported by the provider) | `GET /llm-calls` |
| `performance` | Iterations, latency, TTFT, total tokens, number of tool calls, duplicate identical calls | Client timing, SSE, `usage` |
| `judge:*` | `correctness`, `groundedness` (did the answer use the tool results?), `safety` | LangChain chat model with structured output |

The `context` checks tell you *why* a context case failed. If the fact never reached the prompt, it was lost
by history handling, compression or isolation. If the fact was in the prompt and the answer was still wrong,
the model ignored it.

## Setup

These commands are for Windows PowerShell and are run from the repository root.

```powershell
cd agent-eval
uv venv
uv pip install -e ".[dev,otlp]"
```

## Run

Start the backend as usual (for example on `http://127.0.0.1:8000`), then run:

```powershell
$env:UNIBOT_EVAL_BASE_URL = "http://127.0.0.1:8000"
# Judge: any OpenAI-compatible endpoint. If these are unset, the backend's llm_* variables are reused.
$env:EVAL_JUDGE_MODEL    = "deepseek-v4-flash"
$env:EVAL_JUDGE_BASE_URL = "https://api.deepseek.com"
$env:EVAL_JUDGE_API_KEY  = "<key>"

uv run python -m unibot_eval list                                  # show the cases
uv run python -m unibot_eval run --k 3 --out reports/latest        # full suite, 3 attempts per case
uv run python -m unibot_eval run --tags context,tool_use --no-judge # deterministic checks only
uv run python -m unibot_eval run --case "builtin.*" --k 5
```

The command exits with a non-zero code when the pass^k rate is below `--min-pass-rate` (default 1.0), so it
can gate CI directly. Each run writes `report.md` (summary, per-category and per-tag tables, latency
percentiles, and failures with backend trace ids) and `report.json` (every check of every attempt).

### As pytest (JUnit output for CI)

```powershell
uv run pytest tests/test_harness.py                   # offline self-tests of the harness (no backend)
$env:UNIBOT_EVAL_REPEATS = "3"
uv run pytest -m live --junitxml=reports/junit.xml    # each dataset case is one test, pass^k required
uv run pytest -m live -k "context or safety"
```

Set `UNIBOT_EVAL_JUDGE=false` to skip judge checks in pytest.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `UNIBOT_EVAL_BASE_URL` | `http://127.0.0.1:8000` | Backend URL |
| `UNIBOT_EVAL_REPEATS` | `1` | k, used when neither the case nor `--k` sets it |
| `UNIBOT_EVAL_CONCURRENCY` | `2` | Number of attempts in parallel |
| `UNIBOT_EVAL_STREAM` | `true` | Use `/chat/stream`, which is required to measure TTFT |
| `UNIBOT_EVAL_TIMEOUT` | `180` | Seconds allowed per request |
| `UNIBOT_EVAL_HEADERS` | – | JSON of extra headers, e.g. `{"Cookie": "unibot_session=..."}` when auth is enforced |
| `UNIBOT_EVAL_TENANT` | `default` | Tenant id for eval users |
| `UNIBOT_EVAL_KEEP_CONVERSATIONS` | `false` | Keep the conversations the eval creates, for debugging |
| `UNIBOT_EVAL_ENABLE` | – | Optional requirements for pytest runs, e.g. `compression` |
| `EVAL_JUDGE_MODEL` / `_BASE_URL` / `_API_KEY` | backend `llm_*` | Judge model |

Every attempt uses fresh user ids (`eval-<case>-<actor>-<random>`) and deletes its conversations when it
finishes, so attempts can't see each other's state.

## OpenTelemetry

The harness uses the OpenTelemetry SDK. Each `eval.turn` span records `unibot.trace_id`,
`gen_ai.conversation.id`, token usage, latency, TTFT and the tool calls. Each check becomes an `eval.check` span
event. Every request carries a `traceparent` header, so a backend that honours W3C context can join the eval
span tree.

```powershell
docker run -d -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one   # or Phoenix / Tempo / Langfuse
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318"
uv run python -m unibot_eval run --k 3
# To print spans to the terminal instead: $env:UNIBOT_EVAL_OTEL_CONSOLE = "1"
```

## Writing cases

A case is a scripted conversation. `{{env.A|B}}` resolves to the first set environment variable. `{{marker}}`, `{{marker2}}`, `{{secret}}` and `{{number}}` get fresh random
values on every attempt.

```yaml
cases:
  - id: builtin.open_aina
    tags: [tool_use, builtin]
    repeats: 3                     # optional per-case k
    turns:
      - user: "打开 unibot-memory 应用"
        actor: a                   # each actor label is a separate user; reuse it to continue the conversation
        # new_conversation: true   # start a fresh conversation for this actor
        # capability: builtin:open_aina   # force the first call (same as the API field)
        expect:
          status: completed        # default; use approval_required for gated calls
          tools: {expected: [open_aina], mode: superset, forbidden: [list_app], allow_failed: false}
          tool_args: [{tool: open_aina, args: {aina_id: unibot-memory}}]
          output: {contains: [...], not_contains: [...], regex: "..."}
          widgets: [{kind: navigation}]
          prompt: {contains: ["{{marker}}"], not_contains: [...], has_summary: true}
          trace: {events_include: [approval.required]}
          budget: {max_iterations: 3, max_latency_ms: 30000, max_ttft_ms: 5000, max_total_tokens: 40000,
                   max_tool_calls: 2, max_duplicate_calls: 0}
          judge: {rubric: groundedness, criteria: "...", reference: "...", threshold: 0.7}
      - action: confirm_approval   # or deny_approval; acts on this actor's last pending approval
      - action: http               # API setup step; {{user_id}}/{{tenant_id}} are this actor's ids
        request: {method: POST, path: /model-settings/providers, body: {user_id: "{{user_id}}"},
                  save: {provider_id: id}}   # saved values are available to later steps as {{provider_id}}
      - action: poll               # wait for a background job to settle
        poll: {path: "/documents/x.md/edit-tasks", params: {user_id: "{{user_id}}"},
               field: items.0.status, until: [reviewing, failed], equals: reviewing, timeout_s: 180}
      - user: "..."
        expect:
          llm_calls: {served_model: mimo-v2.5}   # model that actually served the agent's calls
          # status: failed + error: "..."        # the turn must end with this backend error
```

Capability names are the trace `target_id` values (`list_app`, `describe_aina`, `open_aina`,
`request_clarification`, `memory.remember`, `memory.recall`, `memory.forget`, remote `tool_id`s). The model-facing
function name is also accepted.

Try to use the cheapest check that can decide a question. Exact facts, ids and tool names belong in
deterministic checks. Save `judge` for open-ended quality. Build new cases from real production failures:
every bug you fix should become a case.

## Included datasets

| File | Covers |
|---|---|
| `tool_use.yaml` | No false tool calls, `list_app` / `describe_aina` / `open_aina` / clarification form, argument extraction, redundant calls |
| `context.yaml` | Recall across turns, latest correction wins, facts surviving several turns, clean new conversations, cross-user isolation, compression (opt-in) |
| `safety.yaml` | Memory write, recall, deny and confirm lifecycle through the approval gate; prompt injection in user data; system-prompt exfiltration (a policy case, adjust to taste) |
| `robustness.yaml` | Unknown app, ambiguous request, missing document: no fabricated success |
| `jobs.yaml` | Document edit jobs reach review with a draft; the model selected in model settings serves chat (by the provider-reported model and black-box with a deliberately broken provider). Provider credentials come from `EVAL_PROVIDER_BASE_URL/API_KEY`, falling back to `EVAL_JUDGE_*` then `llm_*` |
| `performance.yaml` | Latency, TTFT and token budgets. **These numbers are placeholders.** Set them from a baseline run of your deployment |

**Compression case:** this needs a backend with a small context window so that compaction actually triggers,
for example `UNIBOT_CONTEXT_WINDOW_TOKENS=6000` and `UNIBOT_CONTEXT_COMPRESSION_KEEP_RECENT_TURNS=2` on a
dedicated eval instance. Then run `python -m unibot_eval run --enable compression --case "context.compression*"`.

## Reading results

* **pass^k well below pass@k** means the agent *can* do the task but isn't reliable. Look for prompt or
  temperature sensitivity.
* **Low `tool_selection`, high `output`** means the answers look right but come from the wrong path. This
  usually shows up as a later regression.
* **A `context` check fails on `prompt:contains`** means history handling or compression dropped the fact. If
  only `output` fails, the model ignored a fact it was given.
* The backend trace ids for every failure are listed in `report.md`. Open them in the platform's trace view.

## Layout

```
unibot_eval/
  client.py      REST + SSE client (TTFT), trace polling, /llm-calls lookup
  trajectory.py  trace -> ordered capability calls (events merged with OTel spans), prompt extraction
  checks.py      deterministic scorers
  judge.py       LangChain LLM-as-judge (correctness / groundedness / safety)
  runner.py      k attempts, actors, approvals, cleanup, OTel spans, pass@k / pass^k
  report.py      aggregation, report.md / report.json
  telemetry.py   OTel provider, OTLP / console exporters, traceparent injection
  cli.py         python -m unibot_eval
datasets/        YAML cases
tests/           test_harness.py (offline, uses fake_backend.py) and test_live_cases.py (-m live)
```
