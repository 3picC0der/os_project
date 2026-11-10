/*
 * main.c - vmsim command-line interface.
 *
 *   vmsim run     -p fifo|lru|clock|fifo-r -f FRAMES [-l PREFIX] [-c] TRACE
 *       Runs one policy and prints per-process and system tables.
 *       -l PREFIX writes PREFIX_steps.csv and PREFIX_warnings.csv.
 *       -c checks every invariant after every reference (slow on big traces).
 *
 *   vmsim compare TRACE FRAMES_LIST
 *       Runs FIFO, LRU, Clock and FIFO-Reinsertion for each frame count in a comma-separated
 *       list and prints one CSV row per run. A count may be a percentage of
 *       the trace's distinct pages, e.g. "2,3,4,8,0.1%,10%".
 *       Any FIFO fault-count increase between adjacent frame counts
 *       (Belady's anomaly) is reported on stderr.
 */
#define _POSIX_C_SOURCE 200809L   /* getopt, strdup */
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "sim.h"
#include "trace.h"

static void usage(void)
{
    fprintf(stderr,
        "usage: vmsim run -p fifo|lru|clock|fifo-r -f FRAMES [-l PREFIX] [-c] TRACE\n"
        "       vmsim compare TRACE FRAMES[,FRAMES...]   (FRAMES may be N or P%%)\n");
    exit(2);
}

/* Frame count: a positive integer, or a percentage of distinct pages (at least 1). */
static int parse_frames(const char *s, int npages, int *out)
{
    long long v;
    size_t n = strlen(s);
    if (n > 1 && s[n - 1] == '%') {
        char *end;
        double pct = strtod(s, &end);
        if (end != s + n - 1 || !(pct > 0) || pct > 100)
            return -1;
        v = llround(npages * pct / 100.0);
        *out = v < 1 ? 1 : (int)v;
        return 0;
    }
    if (parse_int(s, 1, INT_MAX, &v) != 0)
        return -1;
    *out = (int)v;
    return 0;
}

static double rate(long part, long whole) { return whole ? (double)part / whole : 0.0; }

/* Formats a step number, or "-" for "never". */
static const char *step_str(long step, char *buf, size_t n)
{
    if (step == 0)
        snprintf(buf, n, "-");
    else
        snprintf(buf, n, "%ld", step);
    return buf;
}

static void print_report(const Sim *s)
{
    char buf[32];
    const Trace *t = s->t;
    printf("policy %s, %d frames, %ld references, %d processes, %d distinct pages\n\n",
           policy_name(s->policy), s->nframes, t->n, t->nprocs, t->npages);
    printf("%6s %9s %9s %9s %8s %9s %9s %8s %11s\n",
           "pid", "refs", "hits", "faults", "fault%", "page-ins", "evict-wb", "flushes", "first-warn");
    for (int i = 0; i < t->nprocs; i++) {
        const Proc *p = &s->procs[i];
        printf("%6d %9ld %9ld %9ld %7.2f%% %9ld %9ld %8ld %11s\n",
               t->proc_pid[i], p->refs, p->hits, p->faults, 100 * rate(p->faults, p->refs),
               p->faults, p->writebacks, p->flushes, step_str(p->first_warn, buf, sizeof buf));
    }
    printf("%6s %9ld %9ld %9ld %7.2f%% %9ld %9ld %8ld %11s\n\n",
           "all", s->step, s->hits, s->faults, 100 * rate(s->faults, s->step),
           s->faults, s->writebacks, s->flushes, step_str(s->first_sys_warn, buf, sizeof buf));
    if (t->n == 0) {
        printf("empty trace: rates N/A, no warnings\n");
        return;
    }
    printf("frame utilization: mean %.3f, peak %.3f\n", s->util_sum / s->step, s->util_peak);
    printf("thrashing warning (heuristic): on for %ld of %ld steps\n", s->sys_warn_steps, s->step);
}

static int cmd_run(int argc, char **argv)
{
    Policy policy = LRU;
    int have_policy = 0, check = 0, opt;
    const char *frames_arg = NULL, *prefix = NULL;

    while ((opt = getopt(argc, argv, "p:f:l:c")) != -1) {
        switch (opt) {
        case 'p':
            if (policy_parse(optarg, &policy) != 0) {
                fprintf(stderr, "unknown policy '%s' (use fifo, lru, clock or fifo-r)\n", optarg);
                return 2;
            }
            have_policy = 1;
            break;
        case 'f': frames_arg = optarg; break;
        case 'l': prefix = optarg; break;
        case 'c': check = 1; break;
        default: usage();
        }
    }
    if (!have_policy || !frames_arg || optind != argc - 1)
        usage();

    Trace t;
    if (trace_load(argv[optind], &t) != 0)
        return 1;
    int nframes;
    if (parse_frames(frames_arg, t.npages, &nframes) != 0) {
        fprintf(stderr, "invalid frame count '%s': must be a positive integer or a percentage\n",
                frames_arg);
        trace_free(&t);
        return 2;
    }

    Sim s;
    if (sim_init(&s, &t, policy, nframes) != 0) {
        fprintf(stderr, "out of memory\n");
        trace_free(&t);
        return 1;
    }
    if (prefix) {
        char path[1024];
        snprintf(path, sizeof path, "%s_steps.csv", prefix);
        s.steplog = fopen(path, "w");
        snprintf(path, sizeof path, "%s_warnings.csv", prefix);
        s.warnlog = fopen(path, "w");
        if (!s.steplog || !s.warnlog) {
            perror(prefix);
            return 1;
        }
        fprintf(s.steplog, "step,pid,page,op,result,frame,ws,fault_rate,demand_pre,demand_post,used,sys_warn\n");
        fprintf(s.warnlog, "step,pid,event,ws,fault_rate,demand\n");
    }

    int rc = 0;
    while (s.step < t.n) {
        sim_step(&s);
        if (check && sim_check(&s) != 0) {
            rc = 1;
            break;
        }
    }
    if (rc == 0)
        print_report(&s);
    if (s.steplog) fclose(s.steplog);
    if (s.warnlog) fclose(s.warnlog);
    sim_free(&s);
    trace_free(&t);
    return rc;
}

static int cmd_compare(int argc, char **argv)
{
    if (argc != 3)
        usage();
    Trace t;
    if (trace_load(argv[1], &t) != 0)
        return 1;

    /* Parse the whole list first, so a bad entry produces no partial results. */
    int frames[64], nf = 0;
    char *list = strdup(argv[2]);
    for (char *tok = strtok(list, ","); tok; tok = strtok(NULL, ",")) {
        if (nf == 64 || parse_frames(tok, t.npages, &frames[nf]) != 0) {
            fprintf(stderr, "invalid frame count '%s'\n", tok);
            free(list);
            trace_free(&t);
            return 2;
        }
        nf++;
    }
    free(list);

    printf("policy,frames,refs,hits,faults,fault_rate,page_ins,evict_writebacks,"
           "completion_flushes,util_mean,util_peak,warn_steps,first_warn\n");
    long fifo_faults[64];
    for (int p = 0; p < NPOLICIES; p++) {
        for (int i = 0; i < nf; i++) {
            Sim s;
            if (sim_init(&s, &t, (Policy)p, frames[i]) != 0) {
                fprintf(stderr, "out of memory\n");
                return 1;
            }
            sim_run(&s);
            printf("%s,%d,%ld,%ld,%ld,%.6f,%ld,%ld,%ld,%.4f,%.4f,%ld,%ld\n",
                   policy_name(s.policy), s.nframes, s.step, s.hits, s.faults,
                   rate(s.faults, s.step), s.faults, s.writebacks, s.flushes,
                   t.n ? s.util_sum / t.n : 0.0, s.util_peak, s.sys_warn_steps, s.first_sys_warn);
            if (p == FIFO)
                fifo_faults[i] = s.faults;
            sim_free(&s);
        }
    }
    for (int i = 1; i < nf; i++)            /* Belady's anomaly check */
        if (frames[i] > frames[i - 1] && fifo_faults[i] > fifo_faults[i - 1])
            fprintf(stderr, "FIFO anomaly: %d frames -> %ld faults, %d frames -> %ld faults\n",
                    frames[i - 1], fifo_faults[i - 1], frames[i], fifo_faults[i]);
    trace_free(&t);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 2)
        usage();
    if (strcmp(argv[1], "run") == 0)
        return cmd_run(argc - 1, argv + 1);
    if (strcmp(argv[1], "compare") == 0)
        return cmd_compare(argc - 1, argv + 1);
    usage();
    return 2;
}
