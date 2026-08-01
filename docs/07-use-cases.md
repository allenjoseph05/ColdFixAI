# What This Finds

**A catalogue of real problems, how each is detected, and what we honestly cannot do**

Organized by the symptom you notice, not by our internal machinery. Each entry gives the symptom, what it usually turns out to be, how the system proves it, and a worked example.

**Read section 10 first if you are evaluating whether this fits your codebase.** It states plainly what we cannot reach.

---

## How anything gets found at all

Everything below comes from composing six kinds of experiment. Not a list of detectors — a list of *ways to construct a contrast between two runs*:

| Primitive | Method | What it reveals |
|---|---|---|
| **Scaling** | run at increasing input size | cost that grows when it shouldn't |
| **Ablation** | remove a component, re-measure | which component owns the cost |
| **Substitution** | swap an implementation or setting | whether an alternative is better |
| **Isolation** | run a part alone vs in context | contention with other work |
| **Observation** | count operations, capture stacks | operations happening too often |
| **Temporal** | run the same test against older commits | which change caused a regression |

The list below is **representative, not exhaustive**. Anything reachable by composing these is in scope even if it does not appear here — which is the point of the design.

---

## 1. Web APIs and backend services

### 1.1 An endpoint gets slower as data grows
**Usually:** N+1 queries — one query per item in a list, from a lazily loaded relationship.
**Found by:** scaling + observation. Query count rises 1:1 with rows.
**Example:** `GET /api/orders` issues 3 queries at 10 orders, 501 at 500. Each order lazily fetches its customer.
**Fix:** eager-load the relationship in the queryset.
**Typical result:** 501 queries → 2. Response time falls proportionally to round-trip latency.

### 1.2 Writes are slow in bulk
**Usually:** inserts or updates issued one at a time inside a loop.
**Found by:** scaling on write count.
**Example:** importing 5,000 rows issues 5,000 INSERT statements plus 5,000 commits.
**Fix:** bulk insert, single transaction.
**Typical result:** 20× to 100×, since per-statement overhead dominates.

### 1.3 An endpoint is slow regardless of how much data it returns
**Usually:** constant work unrelated to the payload — middleware, permission checks, config parsing, an import executed per request.
**Found by:** scaling shows flat cost, then ablation of each layer in turn.
**Example:** every request re-reads and parses a 400 KB YAML config from disk.
**Fix:** load once at startup.

### 1.4 The response is enormous
**Usually:** over-fetching — selecting all columns, or serializing nested objects nobody consumes.
**Found by:** observation on bytes returned versus bytes actually used downstream.
**Example:** a list endpoint returns every user's full profile including a base64 avatar; the UI shows name and ID.
**Fix:** restrict the field set.
**Note:** often the single biggest win for mobile clients, and invisible in server-side timing.

### 1.5 Serialization dominates
**Usually:** per-field computed properties doing real work, or an in-memory filter running per row.
**Found by:** ablation — stub the serializer, measure the drop; then ablate field by field.
**Example:** a `discount_price` field re-filters an in-memory promotion list for every order. Zero extra queries, but O(orders × promotions).
**Fix:** build the lookup once, index it.
**Why this matters:** query counting finds nothing here. Only ablation locates it.

### 1.6 The same query runs repeatedly in one request
**Usually:** different code paths independently fetching the same thing, with no request-level cache.
**Found by:** observation — group queries by SQL text and stack.
**Example:** the current user is loaded 14 times per request by permission checks in different layers.
**Fix:** request-scoped memoization.

### 1.7 Calls to other services run one after another
**Usually:** sequential awaits where no data dependency exists.
**Found by:** observation on outbound calls plus dependency analysis of the call sites.
**Example:** 6 sequential internal API calls at ~80 ms each; 5 have no dependency on one another.
**Fix:** issue them concurrently.
**Typical result:** 480 ms → ~110 ms.

### 1.8 A list endpoint dies on large tables
**Usually:** no pagination, or pagination applied after the full fetch.
**Found by:** scaling — rows returned grows without bound.
**Fix:** limit at the query, not in application code.

### 1.9 Slow only in production, fine locally
**Usually:** configuration, not code — pool sizes, missing index, cache disabled, debug logging enabled.
**Found by:** substitution across configuration values, and query-plan comparison.
**Example:** a connection pool of 5 against 40 concurrent workers; requests spend most of their time waiting for a connection.
**Honesty:** we can identify and prove this in a test environment. We do not touch your production configuration.

### 1.10 Authentication or authorization is expensive
**Usually:** a permission check that issues queries, run per object rather than per request.
**Found by:** ablation of the auth layer, then scaling.
**Example:** row-level permission checks issue one query per row displayed.

### 1.11 File uploads or downloads are slow
**Usually:** the whole file is buffered in memory rather than streamed.
**Found by:** scaling on memory alongside file size.
**Fix:** stream.

### 1.12 Background jobs pile up
**Usually:** per-job setup cost, or a job doing work that belongs in a batch.
**Found by:** scaling on job count, ablation of setup.

---

## 2. Data processing and pipelines

### 2.1 A script that handled 100 MB chokes on 2 GB
**Usually:** quadratic accumulation — appending to a structure that copies on every append.
**Found by:** scaling. Doubling input roughly quadruples time.
**Example:** `df = df.append(row)` inside a loop. Pandas copies the entire frame each call.
**Fix:** accumulate into a list, concatenate once.
**Typical result:** 41 minutes → 90 seconds. One of the most common bugs in real data code.

### 2.2 String building is slow
**Usually:** repeated concatenation in a loop, which reallocates each time.
**Found by:** scaling.
**Fix:** join a list.

### 2.3 The same file is read more than once
**Usually:** separate stages each opening the source independently.
**Found by:** observation on file operations.

### 2.4 Memory grows until the process is killed
**Usually:** the whole dataset is materialized when streaming would work, or a per-row object is retained.
**Found by:** scaling on peak memory.
**Fix:** generators, chunked processing.

### 2.5 Per-row overhead dominates
**Usually:** a Python-level function applied row by row where a vectorized operation exists.
**Found by:** ablation of the row function, then substitution.
**Typical result:** 10× to 100× for numeric work.

### 2.6 Parsing happens more often than necessary
**Usually:** JSON or dates re-parsed on every access instead of once.
**Found by:** observation on parse call counts.

### 2.7 Wrong data types inflate memory
**Usually:** default 64-bit types, or strings where categories would do.
**Found by:** observation on memory per row, substitution to test alternatives.

---

## 3. Command-line tools and startup cost

### 3.1 A CLI takes seconds to print `--help`
**Usually:** heavy module-level imports executed before argument parsing.
**Found by:** ablation on the import list.
**Example:** a module-level `import tensorflow` costs 2.8 of 3.1 seconds, and `--help` never uses it.
**Fix:** move the import inside the function that needs it.
**Note:** affects every invocation, including tab completion and CI. Users feel this constantly.

### 3.2 Startup does network or disk work
**Usually:** config fetched, credentials validated, or a version check performed at import.
**Found by:** ablation, plus observation on network calls before `main()`.

### 3.3 A serverless function has a bad cold start
**Same causes as above**, with a larger blast radius because it recurs per cold invocation.
**Found by:** measuring import cost separately from handler cost.

---

## 4. Libraries and packages

### 4.1 A hot function has the wrong algorithm
**Usually:** a linear scan where a hash lookup would do; a sort inside a loop.
**Found by:** scaling on the function in isolation.
**Fix:** change the data structure.

### 4.2 Defensive copying is expensive
**Usually:** a list or dict copied on every call "to be safe" when the caller never mutates it.
**Found by:** ablation of the copy, then scaling.
**Note:** we flag it and prove the cost; whether the safety guarantee matters is a human decision.

### 4.3 Regexes are compiled per call
**Found by:** observation on compile counts.
**Fix:** module-level compilation.

### 4.4 A dependency is heavier than it needs to be
**Usually:** a large library imported for one small function.
**Found by:** ablation on import cost, substitution with a lighter alternative.
**Honesty:** we can measure the cost and propose an alternative. Whether swapping a dependency is acceptable is your call, not ours.

---

## 5. Test suites and CI

This category is often the highest-value one, because the cost is paid by your developers many times a day.

### 5.1 The suite takes far too long
**Usually:** a small number of tests dominate.
**Found by:** observation on per-test timing.
**Example:** 8 of 200 tests consume 60% of wall time.

### 5.2 Fixtures are rebuilt for every test
**Usually:** function-scoped fixtures doing schema creation or heavy seeding.
**Found by:** ablation — reuse the fixture and measure.
**Example:** 12 minutes → 4 minutes by moving to session scope with transaction rollback.

### 5.3 Tests hit the network
**Found by:** observation on outbound calls during the suite.
**Fix:** stub at the boundary.
**Side benefit:** removes flakiness as well as time.

### 5.4 Tests run serially when they need not
**Found by:** isolation — check for shared state between tests.
**Honesty:** we can identify independence and prove it; enabling parallelism is a change with real risk and stays a human decision.

### 5.5 CI rebuilds everything every run
**Usually:** Docker layer ordering that invalidates the cache on every commit.
**Found by:** temporal comparison of build times plus layer analysis.
**Fix:** reorder so dependencies are cached above source.

---

## 6. Machine learning workloads

### 6.1 The GPU sits idle
**Usually:** data loading starves it.
**Found by:** ablation — replace the loader with a preloaded tensor and measure.
**Example:** 8 minutes per epoch at 22% utilization → 1.4 minutes at 89%.
**Fix:** worker count, prefetch, pinned memory.

### 6.2 Data moves between CPU and GPU too often
**Found by:** observation on transfer counts.

### 6.3 Preprocessing repeats every epoch
**Usually:** transforms recomputed rather than cached.
**Found by:** temporal observation across epochs.

---

## 7. Configuration and infrastructure

Configuration is the most under-examined category in practice, and a frequently cited figure attributes a majority of real performance problems to it rather than to code.

### 7.1 Connection pool sized wrong
**Found by:** substitution across values, measuring wait time.

### 7.2 A missing index
**Found by:** query-plan comparison — a sequential scan where a seek is available.
**Fix proposal:** the index definition, with the plan before and after.
**Honesty:** we propose the migration. Applying schema changes to a live database is your decision.

### 7.3 Caching is misconfigured
**Usually:** TTL too short, key too specific, or the cache never consulted on the hot path.
**Found by:** observation on hit rate, substitution on TTL.

### 7.4 Compression is off
**Found by:** substitution, measuring bytes transferred.

### 7.5 Debug settings left enabled
**Usually:** verbose logging, SQL echo, template auto-reload in a production config.
**Found by:** ablation of the logging layer.
**Note:** cheap to find, sometimes a large win, and slightly embarrassing — which is why nobody looks.

### 7.6 Garbage collection or heap sizing
**Found by:** substitution across settings.
**Honesty:** we can find better values for the workload we measured. Your production workload may differ.

### 7.7 Thread or worker pool sizing
**Found by:** isolation plus substitution across values.

---

## 8. Regressions over time

### 8.1 "It used to be fast"
**Found by:** temporal comparison — bisect performance across commit history.
**Output:** the specific commit, with before and after numbers.
**Why this is powerful:** it requires no understanding of the code at all, and produces the most actionable possible report.

### 8.2 A dependency upgrade slowed things down
**Found by:** temporal comparison across lockfile revisions.

### 8.3 Nothing regressed, but everything got slower
**Usually:** accumulation — no single commit is responsible.
**Found by:** temporal sampling across many commits, showing a gradual slope.
**Honesty:** this identifies the pattern and quantifies the drift. There is no single fix, and we will say so rather than inventing one.

---

## 9. Direct cost reduction

### 9.1 Cloud compute bill driven by inefficiency
Any fix above translates to compute cost. We report the measured factor so you can compute the saving yourself.

### 9.2 Metered third-party API calls
**Found by:** observation on outbound calls, plus scaling to detect per-item calls.
**Example:** a geocoding API called once per address in a batch, uncached, where 60% of addresses repeat.
**Fix:** cache, or use the batch endpoint.
**Note:** this one pays for itself immediately and in cash, not just latency.

### 9.3 Database read replicas added to mask a query problem
**Found by:** the query-count findings above.
**Note:** fixing the N+1 is usually cheaper than the replica.

---

## 10. What we cannot do — read this section

Being clear here is more useful to you than a longer feature list.

### 10.1 Hard limits

| Not possible | Why |
|---|---|
| Reduce network round-trip latency | physical. We reduce the *number* of round trips; their duration is not ours to change |
| Fix cloud noisy neighbours or host contention | we cannot construct a contrasting run |
| Change container CPU quotas or infrastructure sizing | not in the artifact we modify |
| Rewrite your project in a faster language | out of scope; a single hot path is reachable, a project is not |
| Redesign your architecture | that is a decision about what the software should be, not an optimization |
| Work on a system we cannot run | **the hard boundary — see 10.3** |

### 10.2 Deliberately refused

**Concurrency and locking.** We diagnose contention and report it with evidence. We never patch it.

The reason is honest: our correctness check compares outputs for the same inputs. That cannot detect an introduced race condition — a race passes ten thousand times and fails in production. Since we cannot verify safety, we do not make the change. This restriction is what allows us to say "faster without breaking anything" and mean it.

**Third-party code.** We report a cause inside a dependency. We do not patch other people's packages.

### 10.3 Requirements

We need, in a test environment:

- Source access
- The ability to run the project
- A throwaway database, populated with realistic data
- Test-environment configuration

We do **not** need production credentials, production data, write access to your main branch, or network egress.

**The data requirement is the one that blocks people.** If your project has no fixtures or factories and no realistic seed data, we can synthesize rows from your schema — but synthetic data has uniform shape, and uniform shape hides exactly the problems worth finding. If every generated customer has three orders, an N+1 that only hurts customers with three thousand orders stays invisible.

### 10.4 Which frameworks are supported

The method is framework-agnostic. One thin layer is not.

**Universal** — timing, comparison, statistics, growth fitting, screening, all agents and prompts, the evidence chain, every verification gate.

**Language-specific** — how ablation patches a target, how stacks are captured, how allocations are counted.

**Framework-specific (the adapter)** — where counter hooks attach, how workloads are discovered, how data is seeded, which stack frames count as framework-internal, how state resets. A few hundred lines per framework.

| Project type | Status |
|---|---|
| Django + Postgres | supported |
| Flask / FastAPI + SQLAlchemy | adapter planned |
| Any Python project on a DB-API driver | query counting works — the hook sits at the cursor, not the ORM |
| Python library, CLI, or data script | **needs almost no adapter** — the workload is a function call |
| Rails, Spring, Express, other languages | adapter authored in that language, connected over MCP |

**Non-web projects are easier, not harder.** A library has no routes to discover, no fixtures to seed, and no state to reset. The web-framework machinery exists because web applications are the difficult case.

Adding a framework means writing an adapter against a published interface and passing a conformance suite. It does not mean modifying the system.

### 10.5 What a null result means

If we find nothing, we say so:

> *"Screened 9 workloads. None show superlinear growth or unexplained constant cost. No optimization opportunity detected."*

That is a real answer, and we will give it rather than manufacture a finding. Most tools in this space cannot say this, which is worth knowing when you compare them.

### 10.6 Diagnosis reaches further than fixing

In a multi-service system we can measure across boundaries and prove where the cost lives — including in services we have no access to patch.

> *"1.8 of your 2.4 seconds is spent waiting on the payments service, across 12 sequential calls, 9 of which have no dependency on each other."*

We can fix the sequential fan-out on your side. We cannot fix the payments service. The report is still most of the value, and it is what a human consultant would hand you.

---

## 11. What every finding comes with

No finding is delivered as an opinion. Each one carries:

- **The measurements** that proved it, including the hypotheses we ruled out and why
- **The growth curve** across input sizes
- **The exact location** — file and line, reached by runtime evidence rather than inference
- **Before and after numbers** on every axis we varied
- **Guard metrics** showing what did *not* get worse — memory, payload size, query count
- **A regression test** that fails on the old code and passes on the new
- **An adversarial review**, including any case where our first attempt was broken and how

You should be able to evaluate a finding in two minutes without re-deriving our reasoning. If you cannot, we have not done our job.
