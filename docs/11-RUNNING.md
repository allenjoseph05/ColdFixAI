# 11 — INSTALLING, CONFIGURING, AND RUNNING

**For somebody who wants to point ColdFix at a repository.** `09-adapters.md` is
for somebody adding a framework; this is for using the ones that ship.

Read `12-LIMITATIONS.md` before you start. It is short, and it is the difference
between this being useful to you and being a disappointment.

---

## 1. Installing

```bash
uv sync
```

That is the whole of it — Python 3.12+, and `uv` resolves the rest. There is no
service to stand up and nothing to configure globally: everything a run needs is
in one file, per subject.

To check the command is on your path:

```bash
coldfix plan --config coldfix.example.toml
```

That reads the example configuration shipped at the repository root and prints
what a run against it would be given. **It opens no container, no database and no
model client**, so it costs nothing and works on a machine with neither Docker
nor Postgres running.

---

## 2. Configuring

Copy `coldfix.example.toml` to `coldfix.toml` and edit it. Every value is a fact
about *your* subject that this tool will not guess — that is S-7.2's convention,
and a default in a configuration loader is the most invisible way to break it:
the file looks complete and the run measures something nobody asked for.

The sections, and what each is for:

| Section | What it says |
|---|---|
| `[project]` | which repository, at which revision, on which framework |
| `[subject]` | how to run it: interpreter, database, settings, test command, and the entry point to measure |
| `[workload]` | the name and one-line description of what is being driven |
| `[budget]` | the euro ceiling, and the exchange rate it is enforced through |
| `[tokens]` | the measured prefix and prompt token counts |
| `[claim]` | the cost claim a repair must satisfy, and the guards that stop a trade |
| `[sandbox]` | the container image and where worktrees go |
| `[store]` | the knowledge database — **not** the checkpoint database |

Three that are worth reading twice:

**`[project].framework`** must match an adapter exactly. `coldfix plan` lists
what can be driven if it does not, because the usual cause is a typo and the fix
is visible once the alternatives are on screen.

**`[budget].ceiling_eur` is a quoted string, not a number.** A ceiling parsed
from a TOML float is very slightly not the number you wrote, and the one
comparison it is used in is the one that stops a run.

**`[store].url` must not be the checkpoint database.** ADR 003 separates them and
the code enforces it: dropping checkpoints is routine and performed with a `DROP`
that knows nothing about the playbook, so sharing one database makes a routine
operation capable of destroying accumulated knowledge.

---

## 3. Checking the configuration

```bash
coldfix plan
```

reports the resolved adapter, the counters read off its declarations, how many
capabilities it claims, the reset candidates it offers, the ceiling, and — the
one people miss — **whether the framework can actually be grounded**:

```
framework    Django
groundable   yes   (registered: Django)
adapter      DjangoAdapter
```

*Having an adapter* and *being groundable* are different facts. An adapter
implements the eight operations; grounding support is what teaches the system to
take a repository from unknown to a runnable workload. Flask has the first and
not the second. If `groundable` says `no`, the plan says the run would be refused
at fingerprinting, and `09-adapters.md` §6 is what closes the gap.

---

## 4. Running

```bash
coldfix run --spend
```

**`--spend` is required and there is no prompt.** Prompts get answered by habit;
this has to be typed on purpose. The run makes paid model calls — `04-cost.md`
§12.3 prices an engineered run at roughly $15 for five findings, and the worst
case it engineers away from is far higher.

`ANTHROPIC_API_KEY` must be set. It is checked *before* anything opens, so a
missing credential is a message rather than a container and a database stood up
for nothing.

### What `run` does today

It refuses, and says why.

Everything up to the point of handing the configured values to `campaign_for` is
built and tested. The handing-over is `S-17.1` — **a run against a real subject
that has never happened.** Wiring that path and shipping it unexecuted would mean
the first real invocation carried the confidence of code nobody had run, which is
the failure this project has recorded at every join between its parts.

So the command tells you that, rather than discovering it at your expense.

---

## 5. What a run needs from you

From `07-use-cases.md` §10.3, and none of it is negotiable:

- Source access
- The ability to run the project
- A throwaway database with realistic data
- Test-environment configuration

Not needed: production credentials, production data, write access to your main
branch, or network egress.

**The data requirement is the one that blocks people.** A subject with no
fixtures, no factories and no realistic seed data can sometimes be synthesized
from its schema, and sometimes cannot — and a measurement against unrealistic
data is a measurement of the wrong program.

---

## 6. Where things go wrong

| Symptom | Cause |
|---|---|
| `no configuration at ...` | no `coldfix.toml`; copy `coldfix.example.toml` |
| `[section].key is required` | that field is missing; the message names both |
| `no adapter implements '...'` | `[project].framework` does not match; the message lists what does |
| `groundable no` | the framework has an adapter and no registered grounding support |
| `was not given --spend` | `run` refuses by default; `plan` answers most questions for free |
| `ANTHROPIC_API_KEY is not set` | checked before anything opens |
| refuses to start against a database | the production guard; a URL that looks like production is refused by design |
