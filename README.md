# Gradescope MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that enables AI assistants to interact with [Gradescope](https://www.gradescope.com/) for course management, grading, regrade reviews, and more.

Built for **instructors and TAs** who want to use AI agents (Claude, Gemini, etc.) for grading workflows.

## Features

### Tools (32 total)

#### Core
| Tool | Description | Access |
|------|-------------|--------|
| `tool_list_courses` | List all courses (instructor & student) | All |
| `tool_get_assignments` | Get assignments for a course | All |
| `tool_get_assignment_details` | Get details of a specific assignment | All |
| `tool_upload_submission` | Upload files to an assignment | All |

#### Instructor/TA Management
| Tool | Description |
|------|-------------|
| `tool_get_course_roster` | Full course roster grouped by role |
| `tool_get_extensions` | View student extensions |
| `tool_set_extension` | Add/update student extensions |
| `tool_modify_assignment_dates` | Change assignment dates |
| `tool_rename_assignment` | Rename an assignment |
| `tool_get_assignment_submissions` | View all submissions |
| `tool_get_student_submission` | Get a student's submission content (text + image URLs + scanned PDF pages, supports Online Assignments) |
| `tool_get_assignment_graders` | View graders for a question |

#### Grading — Read
| Tool | Description |
|------|-------------|
| `tool_get_assignment_outline` | Question hierarchy with IDs, weights, and text |
| `tool_export_assignment_scores` | Per-question scores + summary statistics |
| `tool_get_grading_progress` | Per-question grading dashboard |
| `tool_get_submission_grading_context` | Full grading context: student text answer, rubric items, score, comments, navigation. Supports JSON output. |
| `tool_get_question_rubric` | Get rubric items for a question without needing a submission ID |
| `tool_prepare_grading_artifact` | Save prompt/rubric/reference notes to `/tmp` for context-efficient grading |
| `tool_assess_submission_readiness` | Crop-first read plan + readiness gate for auto-grading |
| `tool_cache_relevant_pages` | Download crop page and adjacent pages to `/tmp` for local/vision review |
| `tool_prepare_answer_key` | Cache an assignment-wide answer key to `/tmp` for repeated grading |
| `tool_smart_read_submission` | Return a tiered crop/full-page/adjacent-page reading plan |

#### Grading — Answer Groups (Batch Grading)
| Tool | Description |
|------|-------------|
| `tool_get_answer_groups` | List AI-clustered answer groups with sizes (markdown/JSON) |
| `tool_get_answer_group_detail` | Inspect group members, crops, and graded status |
| `tool_grade_answer_group` | **Batch-grade all submissions in a group** via `save_many_grades` |

#### Grading — Write ⚠️
| Tool | Description |
|------|-------------|
| `tool_apply_grade` | Apply rubric items, set point adjustment, add comments |
| `tool_create_rubric_item` | Create new rubric item for a question |
| `tool_update_rubric_item` | Update rubric item description or weight (cascading) |
| `tool_delete_rubric_item` | Delete a rubric item (cascading) |
| `tool_get_next_ungraded` | Navigate to the next ungraded submission |

#### Regrade Requests
| Tool | Description |
|------|-------------|
| `tool_get_regrade_requests` | List all pending/completed regrade requests |
| `tool_get_regrade_detail` | Student message, rubric, staff response for a specific regrade |

#### Statistics
| Tool | Description |
|------|-------------|
| `tool_get_assignment_statistics` | Mean/median/std + per-question stats + low-scoring alerts |

### Resources (3)
| URI | Description |
|-----|-------------|
| `gradescope://courses` | Auto-fetched course list |
| `gradescope://courses/{id}/assignments` | Assignment list per course |
| `gradescope://courses/{id}/roster` | Course roster |

### Prompts (7)
| Prompt | Description |
|--------|-------------|
| `summarize_course_progress` | Overview of all assignments and deadlines |
| `manage_extensions_workflow` | Guided extension management |
| `check_submission_stats` | Submission statistics overview |
| `generate_rubric_from_outline` | AI-generated rubric suggestions from assignment structure |
| `grade_submission_with_rubric` | AI-assisted grading of student submissions |
| `review_regrade_requests` | AI review of pending regrade requests vs rubric |
| `auto_grade_question` | Guided question-level auto-grading workflow with confidence gates |

## Quick Start

### 1. Prerequisites
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager

### 2. Install
```bash
git clone https://github.com/Yuanpeng-Li/gradescope-mcp.git
cd gradescope-mcp
cp .env.example .env
# Edit .env with your Gradescope credentials
```

### 3. Configure AI Client

Add to your MCP client configuration (e.g., Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "gradescope": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/gradescope-mcp", "python", "-m", "gradescope_mcp"],
      "env": {
        "GRADESCOPE_EMAIL": "your_email@example.com",
        "GRADESCOPE_PASSWORD": "your_password"
      }
    }
  }
}
```

### 4. Debug with MCP Inspector
```bash
npx @modelcontextprotocol/inspector uv run python -m gradescope_mcp
```

## Project Structure
```
gradescope-mcp/
├── pyproject.toml
├── .env.example
├── src/gradescope_mcp/
│   ├── __init__.py
│   ├── __main__.py          # Entry point
│   ├── server.py            # MCP server + tool/resource/prompt registration
│   ├── auth.py              # Singleton Gradescope auth
│   └── tools/
│       ├── courses.py       # list_courses, get_course_roster
│       ├── assignments.py   # get/modify/rename assignments
│       ├── submissions.py   # upload, view submissions, graders
│       ├── extensions.py    # get/set extensions
│       ├── grading.py       # outline, scores, progress, submission content
│       ├── grading_ops.py   # grading write ops, rubric CRUD, navigation
│       ├── grading_workflow.py  # /tmp artifacts, read strategy, confidence
│       ├── answer_groups.py # batch grading via AI answer groups
│       ├── regrades.py      # regrade request listing + detail
│       ├── statistics.py    # assignment statistics
│       └── safety.py        # write confirmation helpers
├── tests/                   # Automated test suite
└── DEVLOG.md                # Full development history
```

## Key Design Decisions

### Two-Step Write Confirmation
All write-capable tools default to `confirm_write=False`. When called without confirmation, they return a preview of what would change. The agent must explicitly pass `confirm_write=True` to execute the mutation.

### Answer Group Batch Grading
For questions with AI-Assisted Grading enabled, the server can list answer groups and batch-grade entire groups at once via the `save_many_grades` endpoint — reducing O(N) individual grades to O(groups).

### JSON Output Mode
Key read tools (`get_submission_grading_context`, `get_answer_groups`, `get_answer_group_detail`) support `output_format="json"` for reliable agent parsing.

### Smart Reading Strategy
For scanned exams, the server provides a tiered reading plan: crop region first, full page if overflow detected, adjacent pages if answer spans boundaries. A confidence score guides whether the agent should auto-grade, request review, or skip.

## Security
- Credentials are loaded from environment variables only
- `.env` is gitignored
- Uploads require absolute file paths
- All write-capable tools require `confirm_write=True`

## Testing
```bash
uv run pytest -q   # 18 tests
```

## Built With
- [`gradescopeapi`](https://github.com/nyuoss/gradescope-api) — Unofficial Gradescope Python API
- [`mcp`](https://pypi.org/project/mcp/) — Model Context Protocol SDK
- [`python-dotenv`](https://pypi.org/project/python-dotenv/) — Environment variable management
- [`beautifulsoup4`](https://pypi.org/project/beautifulsoup4/) — HTML parsing for reverse-engineered endpoints

## License

MIT
