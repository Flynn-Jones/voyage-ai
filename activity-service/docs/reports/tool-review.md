# MCP Tool Review — activity-service

This reviews whether each tool's name, description and schema are clear enough for an LLM to
pick the right one, and whether the set has gaps or overlaps. The schemas below are what a real
MCP client received from `server.py` over stdio on 2026-09-26 (see [run-report.md](run-report.md)).

## Tool-by-tool

| Tool | Description an MCP client sees | Schema | Assessment |
|---|---|---|---|
| `list_activities` | "List every travel activity with its id, name, type, cost, and duration. Read-only." | no inputs | **Clear.** Listing the fields tells the model this is also how to find an ID from a name. |
| `get_activity` | "Get one travel activity by its numeric activity_id. Read-only." | `activity_id: integer` (required) | **Clear.** "numeric" discourages passing a name. |
| `get_activity_assignments` | "Get the scheduled time assignment(s) for one activity by its numeric activity_id. Read-only." | `activity_id: integer` (required) | **Clear, but can be confused with `get_assignment`**. See Overlaps. |
| `list_assignments` | "List every activity time assignment (assignment_id, activity_id, assignment_time). Read-only." | no inputs | **Clear.** |
| `get_assignment` | "Get one time assignment by its numeric assignment_id. Read-only." | `assignment_id: integer` (required) | **Clear, but can be confused with `get_activity_assignments`**. |

Every description ends in "Read-only.", so a client can see the tools have no side effects.
This is tested (`test_stdio_server_registers_exactly_the_read_only_tools`).

### Improvement over the reference

In the group reference (`ai-services/mcp-server/server.py`), the FastMCP wrappers have no
docstrings and are exposed under `*_tool` names over stdio (`list_expenses_tool`) but bare names
over HTTP. Here, each wrapper uses `@mcp.tool(name=...)` and has a docstring. The result is:
- tool names are identical on stdio, HTTP and in the bridge;
- every tool has a real description.

## Overlaps

1. **`get_activity_assignments(activity_id)` vs `get_assignment(assignment_id)`.** This is the
   pair most likely to be mixed up. Both are about "assignments", and a user saying
   "assignment 4" might mean either ID. Mitigations:
   - the argument names differ and both are required;
   - the tool-selection prompt has an explicit rule ("When is activity N" means
     `get_activity_assignments`) plus an example of each.

   If the model still picks the wrong one, the result is a readable not-found or a
   different record, never a write.
2. **`list_activities` vs `get_activity`.** This isn't a real overlap. When a user names an
   activity instead of giving its ID ("the kayaking one"), the prompt sends the model to
   `list_activities` rather than inventing an ID.

## Gaps

| Gap | Impact | Recommendation |
|---|---|---|
| **No search or filter tool** (by type, maximum cost, duration or name) | Questions like "cheap adventure activities" have to use `list_activities`, which returns everything, leaving the filtering to the caller. That's fine at the current data size. | Add a read-only `search_activities(activity_type?, max_cost?)` if the data grows. AI Mode's `filter_candidates` has logic that could be reused. |
| **No write tools** | This was a deliberate decision in the Plan phase (see [boundary-analysis.md](boundary-analysis.md)). | Only add them if they are switched off by default and require confirmation. |
| **No cross-feature tools** (for example, an activity's destination) | The activities schema has no destination column, so there's nothing to join on yet. | Revisit if the schema gains one. |

## Selection reliability (real models, 2026-09-26, before 21:31 AEST)

Method: `/ask`'s exact selection step was run against local Ollama 0.34.4 at temperature 0, with
the same prompt text, the same `User request:` framing and the same `parse_tool_selection`. There
were 16 cases: the prompt's 5 examples and 11 paraphrases it doesn't contain. "Correct" means the
right tool with the right arguments, or no call when none should happen. A **wrong call** is a
tool that actually ran with the wrong tool or arguments.

| Model | Correct | Wrong calls | Notes |
|---|---|---|---|
| `llama3.1:8b` | **16/16** (11/11 paraphrases) | 0 | Handles "What time does activity 2 start?" → `get_activity_assignments`, and "How much does activity 9 cost?" → `get_activity`. Refuses book, change-price and weather requests. |
| `qwen2.5:0.5b` (the standalone compose's `OLLAMA_MODEL`) | 9/16 | **6** | Mixes up `get_activity_assignments` with `list_assignments` and `get_activity`. Runs `list_activities` for "Book activity 5", "Change the price…" and "What's the weather in Tokyo?". Once produced invalid JSON (`{8}`), which was safely refused. |

**Observed quirks with `llama3.1:8b`:**
- **Vague references.** For "When is my kayaking trip?" it wrote a placeholder ID
  (`<insert id of kayaking activity here>`). The parser rejected it as invalid JSON, so no call
  was made. The outcome is correct, but because of the fail-safe, not because the model followed
  prompt rule 4.
- **The number 999.** "Tell me about activity 999" returned `"none"`, while the same wording with
  42, 250 and 1000 correctly selected `get_activity`. The model seems to treat 999 as a
  placeholder. It's a harmless refusal, and the prompt wasn't changed for this edge case.

**Conclusion:** the overlap and gap analysis above holds up with a capable model. Tool selection
needs a model around 8B parameters; `qwen2.5:0.5b` is not suitable. In every failure seen, the
worst outcome was a read-only call returning the wrong data, shown on the card together with the
raw model output. No write was ever possible.

**Configuration caveat.** `/ask` uses `OLLAMA_INTENT_MODEL`, which is shared with AI Mode's intent
step and defaults to `qwen2.5:7b`. That model isn't pulled on this machine, and neither compose
file overrides the setting. Either `ollama pull qwen2.5:7b` (untested here) or set
`OLLAMA_INTENT_MODEL=llama3.1:8b` for the backend.
