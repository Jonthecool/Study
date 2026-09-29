# Study coach

An AI agent that helps you study for an exam. It reads your notes, quizzes you one
question at a time, explains what you get wrong, makes spaced-repetition
flashcards, tracks which topics you're weak on, and builds a day-by-day plan up to
your exam date.

You can use it two ways:

| | Where it runs | Needs |
|---|---|---|
| **`/study` in Claude Code** | Right here in Claude Code (web, desktop or CLI) | Nothing extra |
| **`python -m study_agent`** | Your own terminal | Python 3.10+ and a Claude API key |

Both save progress to the same file, `.study_data/progress.json`.

## 1. Add your study materials

Put your notes, slides and readings in `materials/`: `.md`, `.txt`, `.pdf`,
`.csv`, `.tex` or `.rst`. There's an example file there you can delete.

## 2a. Use it inside Claude Code

Type `/study` (or just say "quiz me on chapter 3") and Claude follows the
instructions in `.claude/skills/study/SKILL.md`.

On Claude Code on the web the container is temporary, so ask Claude to commit and
push `.study_data/progress.json` at the end of a session to keep your progress.

## 2b. Use the terminal app

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # or run `ant auth login`

python -m study_agent materials/ --exam "BIO 101 Final" --exam-date 2026-10-20
```

Then just chat, or use the shortcuts:

| Command | What it does |
|---|---|
| `/quiz [topic]` | Quiz me, one question at a time |
| `/cards [topic]` | Review flashcards that are due |
| `/make-cards [topic]` | Turn my materials into flashcards |
| `/explain <concept>` | Teach a concept step by step |
| `/plan` | Day-by-day plan up to the exam |
| `/progress` | Accuracy by topic and what to focus on |
| `/exam <YYYY-MM-DD> <name>` | Set the exam |
| `/status` | Exam countdown and cards due (no AI call) |
| `/quit` | Exit |

Options: `--effort low|medium|high|xhigh|max` (how hard Claude thinks, default
`medium`), `--model` (default `claude-opus-5-5`), `--data-dir` (default
`.study_data`).

### How it works

- `study_agent/agent.py` streams a conversation with Claude and runs a tool loop.
  Claude has tools to check the date (New York time) and exam countdown, save and
  review flashcards, record quiz results and read a progress report.
- `study_agent/store.py` keeps everything in a JSON file. Flashcards use Leitner
  boxes: a correct answer moves a card up a box (next review after 0, 1, 3, 7, then
  14 days); a miss sends it back to box 1.
- Your materials are sent once at the start of the session and prompt-cached, so
  later turns are cheaper.
- If Claude's safety filters decline a request, the API retries it on a fallback
  model automatically (`fallbacks: "default"`).

## Tests

```bash
pip install pytest
python -m pytest
```
