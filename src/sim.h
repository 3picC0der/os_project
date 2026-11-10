/*
 * sim.h - the virtual-memory simulator.
 *
 * Model (from the project proposal):
 *   - F physical frames shared by all processes (global replacement, no quotas).
 *   - A fault takes the lowest-index free frame; if none is free the policy
 *     (FIFO, LRU, Clock or FIFO-Reinsertion) picks a victim from any process.
 *   - Every fault is one page-in. Evicting a dirty page is one write-back.
 *   - When a process issues its last reference it finishes: its dirty pages are
 *     flushed (counted apart from eviction write-backs) and its frames are freed.
 *   - Each process keeps a window of its last WINDOW references, giving its
 *     working-set size (distinct pages) and its window fault rate.
 *   - Thrashing warning (a simulation heuristic, not a real OS detector):
 *     a process warns when it has >= WINDOW references, total demand D
 *     (sum of active working sets) > F, and window fault rate >= 0.50.
 */
#ifndef SIM_H
#define SIM_H

#include <stdio.h>
#include "trace.h"

#define WINDOW 20   /* per-process window size for working set and fault rate */

/* CLOCK follows the proposal: a page load sets the reference bit.
 * FIFO_R is the HotOS'23 paper's FIFO-Reinsertion: identical, except a load
 * leaves the bit at 0, so a page only survives the hand if it was hit. */
typedef enum { FIFO, LRU, CLOCK, FIFO_R } Policy;
#define NPOLICIES 4

typedef struct {
    int key;          /* resident (pid, page) key, or -1 if the frame is free */
    int dirty;        /* page was written since it was loaded                  */
    int ref;          /* Clock reference bit                                   */
    int prev, next;   /* FIFO/LRU queue links (-1 = none)                      */
} Frame;

typedef struct {
    /* counters */
    long refs, hits, faults;  /* faults == page-ins                              */
    long writebacks;          /* this process's dirty pages written on eviction  */
    long flushes;             /* this process's dirty pages flushed at finish    */
    /* sliding window over this process's own last WINDOW references */
    int  win_key[WINDOW];
    char win_fault[WINDOW];
    int  win_len, win_pos, win_faults;
    int  ws;                  /* working-set size: distinct pages in the window   */
    /* state */
    int  active, done, warn;
    long first_warn;          /* step (1-based) of first warning, 0 = never      */
    long warn_steps;          /* number of steps with this process warning       */
} Proc;

typedef struct {
    Policy       policy;
    int          nframes;
    const Trace *t;

    Frame *frames;
    int   *where;             /* key -> frame holding it, or -1                 */
    Proc  *procs;
    int    head, tail;        /* FIFO/LRU queue: head is the next victim        */
    int    hand;              /* Clock hand                                     */
    int    used;              /* occupied frames                                */
    int    free_hint;         /* every frame below this index is occupied       */

    long step;                /* references processed so far                    */
    long demand;              /* D: sum of working-set sizes of active procs    */
    long hits, faults, writebacks, flushes;
    double util_sum;          /* sum of used/F, sampled after each reference    */
    double util_peak;
    long sys_warn_steps;      /* steps on which any process warned              */
    long first_sys_warn;      /* first such step (1-based), 0 = never           */

    FILE *steplog;            /* optional per-step CSV log                      */
    FILE *warnlog;            /* optional warning ON/OFF transition log         */
} Sim;

int  sim_init(Sim *s, const Trace *t, Policy p, int nframes);
void sim_step(Sim *s);        /* processes reference number s->step            */
void sim_run(Sim *s);         /* processes the whole trace                      */
void sim_free(Sim *s);

/* Checks every invariant of the model; prints the first violation and
 * returns -1, or returns 0 when everything holds. */
int  sim_check(const Sim *s);

/* The warning rule on its own, so it can be unit-tested. */
int  sim_warn_rule(long refs, long demand, int nframes, int win_faults, int win_len);

const char *policy_name(Policy p);
int  policy_parse(const char *s, Policy *p);

#endif
