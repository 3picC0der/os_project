/*
 * test_sim.c - unit tests for the simulator.
 *
 * Covers every case in the proposal's test table, the feedback's cleanup /
 * Clock-hand test, the warning-rule boundaries, the phase-change workload,
 * and random traces with all invariants checked after every reference.
 *
 * Notation in comments: "0R" = page 0 read by PID 1.
 * H = hits, M = faults, E = eviction write-backs, C = completion flushes.
 */
#define _POSIX_C_SOURCE 200809L   /* fmemopen */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sim.h"
#include "trace.h"

static int checks, failures;

#define CHECK(cond) do {                                                   \
        checks++;                                                          \
        if (!(cond)) {                                                     \
            failures++;                                                    \
            printf("    FAIL line %d: %s\n", __LINE__, #cond);              \
        }                                                                  \
    } while (0)

/* Parses CSV text (header included) into a trace. Returns 0 if valid. */
static int parse(const char *csv, Trace *t)
{
    FILE *f = fmemopen((void *)csv, strlen(csv), "r");
    if (!f)
        return -1;
    int rc = trace_read(f, "test", t);
    fclose(f);
    return rc;
}

/* Builds a PID-1 trace from short notation, e.g. "0R 1R 0W". */
static int parse_short(const char *refs, Trace *t)
{
    static char buf[65536];
    strcpy(buf, "pid,page,op\n");
    char page[32];
    for (const char *p = refs; *p; ) {
        if (*p == ' ') { p++; continue; }
        int n = 0;
        while (*p >= '0' && *p <= '9')
            page[n++] = *p++;
        page[n] = '\0';
        sprintf(buf + strlen(buf), "1,%s,%c\n", page, *p++);
    }
    return parse(buf, t);
}

/* Runs a whole trace, checking every invariant after every reference. */
static void run_checked(Sim *s, const Trace *t, Policy p, int frames)
{
    sim_init(s, t, p, frames);
    while (s->step < t->n) {
        sim_step(s);
        if (sim_check(s) != 0) {
            CHECK(!"invariant violated");
            break;
        }
    }
}

/* Runs `trace` under each policy in `policies` and checks H, M, E, C. */
static void expect(const char *name, const char *trace, int is_short, int frames,
                   const Policy *policies, int npol, long H, long M, long E, long C)
{
    printf("  %s\n", name);
    Trace t;
    int rc = is_short ? parse_short(trace, &t) : parse(trace, &t);
    CHECK(rc == 0);
    if (rc != 0)
        return;
    for (int i = 0; i < npol; i++) {
        Sim s;
        run_checked(&s, &t, policies[i], frames);
        if (s.hits != H || s.faults != M || s.writebacks != E || s.flushes != C)
            printf("    %s: got H=%ld M=%ld E=%ld C=%ld\n", policy_name(policies[i]),
                   s.hits, s.faults, s.writebacks, s.flushes);
        CHECK(s.hits == H && s.faults == M && s.writebacks == E && s.flushes == C);
        CHECK(s.used == 0);                       /* every process cleaned up at the end */
        sim_free(&s);
    }
    trace_free(&t);
}

static const Policy ALL[] = { FIFO, LRU, CLOCK, FIFO_R };
static const Policy ONLY_FIFO[] = { FIFO };
static const Policy ONLY_LRU[] = { LRU };
static const Policy ONLY_CLOCK[] = { CLOCK };
static const Policy ONLY_FIFO_R[] = { FIFO_R };

static void test_proposal_table(void)
{
    printf("proposal test table\n");
    expect("one frame, clean eviction",  "0R 1R 0R", 1, 1, ALL, 4, 0, 3, 0, 0);
    expect("repeated page",              "0R 0W 0R", 1, 1, ALL, 4, 2, 1, 0, 1);
    expect("dirty eviction",             "0W 1R",    1, 1, ALL, 4, 0, 2, 1, 0);
    expect("FIFO vs LRU (FIFO)",  "0R 1R 0R 2R 0R", 1, 2, ONLY_FIFO, 1, 1, 4, 0, 0);
    expect("FIFO vs LRU (LRU)",   "0R 1R 0R 2R 0R", 1, 2, ONLY_LRU,  1, 2, 3, 0, 0);
    expect("Clock bits and hand", "0R 1R 0R 2R 1R", 1, 2, ONLY_CLOCK, 1, 2, 3, 0, 0);
    /* FIFO-Reinsertion loads with bit 0: 2R evicts page 1 (never hit), so 1R misses. */
    expect("FIFO-Reinsertion, same trace", "0R 1R 0R 2R 1R", 1, 2, ONLY_FIFO_R, 1, 1, 4, 0, 0);
    expect("separate page identities",
           "pid,page,op\n1,0,R\n2,0,R\n1,0,R\n2,0,R\n", 0, 2, ALL, 4, 2, 2, 0, 0);
    expect("completion and reuse",
           "pid,page,op\n1,0,W\n2,0,R\n", 0, 1, ALL, 4, 0, 2, 0, 1);
    expect("FIFO anomaly, 3 frames", "1R 2R 3R 4R 1R 2R 5R 1R 2R 3R 4R 5R", 1, 3,
           ONLY_FIFO, 1, 3, 9, 0, 0);
    expect("FIFO anomaly, 4 frames", "1R 2R 3R 4R 1R 2R 5R 1R 2R 3R 4R 5R", 1, 4,
           ONLY_FIFO, 1, 2, 10, 0, 0);

    /* Each PID in "separate page identities" has one fault and one hit. */
    Trace t;
    parse("pid,page,op\n1,0,R\n2,0,R\n1,0,R\n2,0,R\n", &t);
    Sim s;
    run_checked(&s, &t, LRU, 2);
    CHECK(s.procs[0].faults == 1 && s.procs[0].hits == 1);
    CHECK(s.procs[1].faults == 1 && s.procs[1].hits == 1);
    sim_free(&s);
    trace_free(&t);
}

/* Step-by-step Clock state for F=2; 0R 1R 0R 2R 1R.
 * One extra reference (3R) keeps PID 1 alive, so the state after the final
 * 1R can be inspected before the process finishes and frees its frames. */
static void test_clock_state(void)
{
    printf("Clock bits and hand, step by step\n");
    Trace t;
    parse_short("0R 1R 0R 2R 1R 3R", &t);
    Sim s;
    sim_init(&s, &t, CLOCK, 2);
    for (int i = 0; i < 3; i++)
        sim_step(&s);
    CHECK(s.frames[0].ref == 1 && s.frames[1].ref == 1 && s.hand == 0);  /* before 2R */
    sim_step(&s);                                                        /* 2R */
    CHECK(t.key_page[s.frames[0].key] == 2);                             /* page 0 evicted */
    CHECK(s.frames[0].ref == 1 && s.frames[1].ref == 0 && s.hand == 1);
    sim_step(&s);                                                        /* 1R: a hit */
    CHECK(s.frames[1].ref == 1 && s.hand == 1);
    sim_free(&s);
    trace_free(&t);
}

/* Feedback item 4: a process finishes, its frames are freed and reused.
 * F=3: (1,0,W) (2,0,R) (1,1,R) (2,1,R) (2,2,R) (2,3,R) (2,3,R)
 *   after step 3: PID 1 finishes -> flushes page 0, frames 0 and 2 freed, hand stays 0
 *   step 4 reuses frame 0 (lowest free), hand -> 1
 *   step 5 reuses frame 2, hand -> 0
 *   step 6: memory full, all bits 1 -> sweep clears them, evicts frame 0, hand -> 1
 *   step 7: a hit, hand unchanged; then PID 2 finishes and frees everything */
static void test_cleanup_and_reuse(void)
{
    printf("cleanup, frame reuse and Clock hand (feedback item 4)\n");
    Trace t;
    parse("pid,page,op\n1,0,W\n2,0,R\n1,1,R\n2,1,R\n2,2,R\n2,3,R\n2,3,R\n", &t);
    Sim s;
    sim_init(&s, &t, CLOCK, 3);
    for (int i = 0; i < 3; i++)
        sim_step(&s);
    CHECK(sim_check(&s) == 0);
    CHECK(s.used == 1 && s.frames[0].key == -1 && s.frames[2].key == -1);
    CHECK(s.frames[0].ref == 0 && s.frames[2].ref == 0);   /* freed frames' state cleared */
    CHECK(s.hand == 0);                                    /* hand position retained */
    CHECK(s.procs[0].done && s.procs[0].flushes == 1 && s.flushes == 1);

    sim_step(&s);
    CHECK(s.where[t.refs[3].key] == 0 && s.hand == 1);
    sim_step(&s);
    CHECK(s.where[t.refs[4].key] == 2 && s.hand == 0);
    sim_step(&s);
    CHECK(s.where[t.refs[5].key] == 0 && s.hand == 1);
    CHECK(sim_check(&s) == 0);
    sim_step(&s);
    CHECK(s.hand == 1);

    /* Per-process totals. */
    CHECK(s.procs[0].refs == 2 && s.procs[0].faults == 2 && s.procs[0].flushes == 1);
    CHECK(s.procs[1].refs == 5 && s.procs[1].faults == 4 && s.procs[1].hits == 1);
    CHECK(s.faults == 6 && s.hits == 1 && s.writebacks == 0 && s.flushes == 1);
    CHECK(s.used == 0);                                    /* PID 2 cleaned up too */
    sim_free(&s);

    /* FIFO and LRU drop the finished process's queue entries. */
    for (int p = FIFO; p <= LRU; p++) {
        run_checked(&s, &t, (Policy)p, 3);
        CHECK(s.faults == 6 && s.flushes == 1 && s.head == -1 && s.tail == -1);
        sim_free(&s);
    }
    trace_free(&t);
}

static void test_empty_and_invalid(void)
{
    printf("empty and invalid inputs (the loader's error messages below are expected)\n");
    Trace t;
    CHECK(parse("pid,page,op\n", &t) == 0 && t.n == 0);
    Sim s;
    run_checked(&s, &t, LRU, 4);
    CHECK(s.hits == 0 && s.faults == 0 && s.first_sys_warn == 0);
    sim_free(&s);
    trace_free(&t);

    const char *bad[] = {
        "",                               /* zero-byte file: no header */
        "pid,page\n1,0\n",                /* wrong header */
        "page,pid,op\n0,1,R\n",           /* wrong header order */
        "pid,page,op\n1,0\n",             /* too few fields */
        "pid,page,op\n1,0,R,5\n",         /* too many fields */
        "pid,page,op\n0,0,R\n",           /* pid must be positive */
        "pid,page,op\n-1,0,R\n",
        "pid,page,op\nx,0,R\n",
        "pid,page,op\n1,-3,R\n",          /* page must be >= 0 */
        "pid,page,op\n1,2.5,R\n",
        "pid,page,op\n1,0,X\n",           /* bad op */
        "pid,page,op\n1,0,r\n",           /* lowercase op */
        "pid,page,op\n1,0,R\n\n1,1,R\n",  /* blank data row */
    };
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++)
        CHECK(parse(bad[i], &t) == -1);

    long long v;                          /* frame counts: positive integers only */
    CHECK(parse_int("4", 1, 1000, &v) == 0 && v == 4);
    CHECK(parse_int("0", 1, 1000, &v) == -1);
    CHECK(parse_int("-2", 1, 1000, &v) == -1);
    CHECK(parse_int("2.5", 1, 1000, &v) == -1);
    CHECK(parse_int("abc", 1, 1000, &v) == -1);
    CHECK(parse_int("", 1, 1000, &v) == -1);
}

/* Hand-calculated boundaries of the warning rule (WINDOW = 20, F = 4). */
static void test_warning_rule(void)
{
    printf("warning rule boundaries\n");
    CHECK(sim_warn_rule(19, 5, 4, 19, 19) == 0);   /* 19 references: too few */
    CHECK(sim_warn_rule(20, 5, 4, 20, 20) == 1);   /* 20 references: warns   */
    CHECK(sim_warn_rule(20, 4, 4, 20, 20) == 0);   /* D == F: no warning     */
    CHECK(sim_warn_rule(20, 5, 4, 20, 20) == 1);   /* D >  F: warns          */
    CHECK(sim_warn_rule(20, 5, 4, 9, 20) == 0);    /* fault rate 0.45        */
    CHECK(sim_warn_rule(20, 5, 4, 10, 20) == 1);   /* fault rate 0.50        */

    /* In a real run: F=1, pages 0 and 1 alternate, every reference faults.
     * The warning must be off after 19 references and on after 20. */
    char buf[1024] = "pid,page,op\n";
    for (int i = 0; i < 25; i++)
        sprintf(buf + strlen(buf), "1,%d,R\n", i % 2);
    Trace t;
    parse(buf, &t);
    Sim s;
    sim_init(&s, &t, FIFO, 1);
    for (int i = 0; i < 19; i++)
        sim_step(&s);
    CHECK(s.procs[0].warn == 0);
    sim_step(&s);
    CHECK(s.procs[0].warn == 1 && s.first_sys_warn == 20);
    sim_free(&s);
    trace_free(&t);
}

/* A finished process leaves the total demand D, and its warning clears. */
static void test_demand_after_finish(void)
{
    printf("finished process removed from demand\n");
    Trace t;
    parse("pid,page,op\n1,0,R\n1,1,R\n1,2,R\n2,0,R\n2,1,R\n2,0,R\n", &t);
    Sim s;
    sim_init(&s, &t, LRU, 8);
    sim_step(&s); sim_step(&s);
    CHECK(s.demand == 2);
    sim_step(&s);                          /* PID 1's last reference: ws 3, then cleanup */
    CHECK(s.demand == 0 && s.procs[0].done && !s.procs[0].active);
    sim_step(&s); sim_step(&s);
    CHECK(s.demand == 2 && sim_check(&s) == 0);
    sim_free(&s);
    trace_free(&t);
}

/* The proposal's changing-locality workload with F = 4:
 * 80 refs on pages 0-1, 160 refs cycling 0-7, 80 refs on pages 0-1. */
static void test_phase_workload(void)
{
    printf("changing-locality workload\n");
    char *buf = malloc(8192);
    strcpy(buf, "pid,page,op\n");
    for (int i = 0; i < 80; i++)  sprintf(buf + strlen(buf), "1,%d,R\n", i % 2);
    for (int i = 0; i < 160; i++) sprintf(buf + strlen(buf), "1,%d,R\n", i % 8);
    for (int i = 0; i < 80; i++)  sprintf(buf + strlen(buf), "1,%d,R\n", i % 2);
    Trace t;
    parse(buf, &t);
    free(buf);

    for (int p = 0; p < NPOLICIES; p++) {
        Sim s;
        sim_init(&s, &t, (Policy)p, 4);
        int warned_in_phase1 = 0, warned_in_phase2 = 0;
        while (s.step < 260) {
            sim_step(&s);
            if (s.step <= 80)       warned_in_phase1 |= s.procs[0].warn;
            else if (s.step <= 240) warned_in_phase2 |= s.procs[0].warn;
        }
        CHECK(!warned_in_phase1);
        CHECK(warned_in_phase2);
        CHECK(s.procs[0].ws == 2 && s.procs[0].warn == 0);   /* 20 refs into phase 3 */
        printf("    %-6s first warning at step %ld (%ld refs into phase 2)\n",
               policy_name((Policy)p), s.procs[0].first_warn, s.procs[0].first_warn - 80);
        sim_free(&s);
    }
    trace_free(&t);
}

/* Random multi-process traces: every invariant after every reference. */
static void test_random_invariants(void)
{
    printf("random traces, invariants after every reference\n");
    unsigned seed = 12345;
    char *buf = malloc(1 << 20);
    for (int round = 0; round < 20; round++) {
        strcpy(buf, "pid,page,op\n");
        size_t len = strlen(buf);
        int nrefs = 500 + round * 50;
        for (int i = 0; i < nrefs; i++) {
            seed = seed * 1103515245u + 12345u;
            int pid = 1 + (seed >> 16) % 4;
            seed = seed * 1103515245u + 12345u;
            int page = (seed >> 16) % (4 + round);
            seed = seed * 1103515245u + 12345u;
            len += sprintf(buf + len, "%d,%d,%c\n", pid, page, (seed >> 16) % 3 ? 'R' : 'W');
        }
        Trace t;
        parse(buf, &t);
        for (int p = 0; p < NPOLICIES; p++)
            for (int f = 1; f <= 9; f += 2) {
                Sim s;
                run_checked(&s, &t, (Policy)p, f);
                CHECK(s.used == 0);
                sim_free(&s);
            }
        trace_free(&t);
    }
    free(buf);
}

int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);   /* keep output in order with stderr */
    test_proposal_table();
    test_clock_state();
    test_cleanup_and_reuse();
    test_empty_and_invalid();
    test_warning_rule();
    test_demand_after_finish();
    test_phase_workload();
    test_random_invariants();
    printf("\n%d checks, %d failed\n", checks, failures);
    return failures ? 1 : 0;
}
