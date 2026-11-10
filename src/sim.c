/*
 * sim.c - frames, the replacement policies, process cleanup,
 * working-set windows and thrashing warnings.
 *
 * FIFO and LRU share one doubly linked queue of frames:
 *   - a newly loaded page is appended at the tail;
 *   - LRU also moves a page to the tail on every hit (FIFO does not);
 *   - the victim is always the head.
 * Clock and FIFO-Reinsertion keep a reference bit per frame and a hand that
 * sweeps the frames; they differ only in the bit given to a newly loaded page.
 */
#include "sim.h"

#include <stdlib.h>
#include <string.h>

const char *policy_name(Policy p)
{
    static const char *names[NPOLICIES] = { "FIFO", "LRU", "CLOCK", "FIFO-R" };
    return names[p];
}

/* Clock-style policies use the hand instead of the queue. */
static int uses_hand(Policy p) { return p == CLOCK || p == FIFO_R; }

int policy_parse(const char *s, Policy *p)
{
    if (strcmp(s, "fifo") == 0 || strcmp(s, "FIFO") == 0)   { *p = FIFO;  return 0; }
    if (strcmp(s, "lru") == 0 || strcmp(s, "LRU") == 0)     { *p = LRU;   return 0; }
    if (strcmp(s, "clock") == 0 || strcmp(s, "CLOCK") == 0) { *p = CLOCK; return 0; }
    if (strcmp(s, "fifo-r") == 0 || strcmp(s, "FIFO-R") == 0) { *p = FIFO_R; return 0; }
    return -1;
}

int sim_warn_rule(long refs, long demand, int nframes, int win_faults, int win_len)
{
    return refs >= WINDOW && demand > nframes && 2 * win_faults >= win_len;
}

/* ---------- FIFO/LRU queue ---------- */

static void queue_push(Sim *s, int f)            /* append at the tail */
{
    s->frames[f].prev = s->tail;
    s->frames[f].next = -1;
    if (s->tail >= 0) s->frames[s->tail].next = f;
    else              s->head = f;
    s->tail = f;
}

static void queue_remove(Sim *s, int f)
{
    Frame *fr = &s->frames[f];
    if (fr->prev >= 0) s->frames[fr->prev].next = fr->next;
    else               s->head = fr->next;
    if (fr->next >= 0) s->frames[fr->next].prev = fr->prev;
    else               s->tail = fr->prev;
    fr->prev = fr->next = -1;
}

/* ---------- frame management ---------- */

/* Empties frame f. A dirty page costs one write: an eviction write-back
 * or, when its process has finished, a completion flush. */
static void release_frame(Sim *s, int f, int is_flush)
{
    Frame *fr = &s->frames[f];
    Proc *owner = &s->procs[s->t->key_proc[fr->key]];
    if (fr->dirty) {
        if (is_flush) { owner->flushes++;    s->flushes++; }
        else          { owner->writebacks++; s->writebacks++; }
    }
    if (!uses_hand(s->policy))
        queue_remove(s, f);
    s->where[fr->key] = -1;
    fr->key = -1;
    fr->dirty = fr->ref = 0;
    s->used--;
    if (f < s->free_hint)
        s->free_hint = f;
}

/* Chooses the frame to evict when memory is full. */
static int pick_victim(Sim *s)
{
    if (!uses_hand(s->policy))
        return s->head;                       /* oldest load (FIFO) / least recent use (LRU) */
    while (s->frames[s->hand].ref) {          /* second chance: clear the bit and move on */
        s->frames[s->hand].ref = 0;
        s->hand = (s->hand + 1) % s->nframes;
    }
    return s->hand;
}

/* Returns a frame for a new page: the lowest free one, or an evicted one. */
static int get_frame(Sim *s)
{
    if (s->used < s->nframes) {
        int f = s->free_hint;
        while (s->frames[f].key >= 0)
            f++;
        s->free_hint = f + 1;
        return f;
    }
    int victim = pick_victim(s);
    release_frame(s, victim, 0);
    s->free_hint = s->nframes;                /* memory will be full again */
    return victim;
}

static void load_page(Sim *s, int f, int key)
{
    Frame *fr = &s->frames[f];
    fr->key = key;
    fr->dirty = 0;
    fr->ref = (s->policy == CLOCK);           /* Clock: a load counts as a reference */
    s->where[key] = f;
    s->used++;
    if (uses_hand(s->policy))
        s->hand = (f + 1) % s->nframes;       /* hand moves past every insertion */
    else
        queue_push(s, f);
}

static void touch_page(Sim *s, int f)         /* a hit */
{
    if (s->policy == LRU) {                   /* LRU: most recently used goes to the tail */
        queue_remove(s, f);
        queue_push(s, f);
    } else if (uses_hand(s->policy)) {
        s->frames[f].ref = 1;                 /* set the bit, hand stays put */
    }                                         /* FIFO: hits change nothing */
}

/* ---------- per-process window and warnings ---------- */

static void window_push(Proc *p, int key, int fault)
{
    if (p->win_len < WINDOW) {
        p->win_key[p->win_len] = key;
        p->win_fault[p->win_len] = (char)fault;
        p->win_len++;
    } else {                                  /* overwrite the oldest entry */
        p->win_faults -= p->win_fault[p->win_pos];
        p->win_key[p->win_pos] = key;
        p->win_fault[p->win_pos] = (char)fault;
        p->win_pos = (p->win_pos + 1) % WINDOW;
    }
    p->win_faults += fault;

    int distinct = 0;                         /* working-set size */
    for (int i = 0; i < p->win_len; i++) {
        int j = 0;
        while (j < i && p->win_key[j] != p->win_key[i])
            j++;
        distinct += (j == i);
    }
    p->ws = distinct;
}

static void set_warning(Sim *s, int proc, int on)
{
    Proc *p = &s->procs[proc];
    if (p->warn == on)
        return;
    p->warn = on;
    if (on && p->first_warn == 0)
        p->first_warn = s->step + 1;
    if (s->warnlog)
        fprintf(s->warnlog, "%ld,%d,%s,%d,%.2f,%ld\n", s->step + 1, s->t->proc_pid[proc],
                on ? "ON" : "OFF", p->ws, p->win_len ? (double)p->win_faults / p->win_len : 0.0,
                s->demand);
}

/* Re-evaluates every active process; total demand affects them all. */
static void update_warnings(Sim *s)
{
    for (int i = 0; i < s->t->nprocs; i++) {
        Proc *p = &s->procs[i];
        if (p->active)
            set_warning(s, i, sim_warn_rule(p->refs, s->demand, s->nframes,
                                             p->win_faults, p->win_len));
    }
}

/* A process finished: flush its dirty pages and free all of its frames.
 * FIFO/LRU forget its queue entries; Clock clears the frames but keeps the hand. */
static void finish_process(Sim *s, int proc)
{
    for (int f = 0; f < s->nframes; f++)
        if (s->frames[f].key >= 0 && s->t->key_proc[s->frames[f].key] == proc)
            release_frame(s, f, 1);

    Proc *p = &s->procs[proc];
    set_warning(s, proc, 0);
    s->demand -= p->ws;
    p->active = 0;
    p->done = 1;
    update_warnings(s);                       /* others re-checked with post-cleanup D */
}

/* ---------- public API ---------- */

int sim_init(Sim *s, const Trace *t, Policy policy, int nframes)
{
    memset(s, 0, sizeof *s);
    s->policy = policy;
    s->nframes = nframes;
    s->t = t;
    s->head = s->tail = -1;
    s->frames = malloc(nframes * sizeof(Frame));
    s->where  = malloc((t->npages + 1) * sizeof(int));
    s->procs  = calloc(t->nprocs + 1, sizeof(Proc));
    if (!s->frames || !s->where || !s->procs) {
        sim_free(s);
        return -1;
    }
    for (int f = 0; f < nframes; f++)
        s->frames[f] = (Frame){ .key = -1, .prev = -1, .next = -1 };
    for (int k = 0; k < t->npages; k++)
        s->where[k] = -1;
    return 0;
}

void sim_step(Sim *s)
{
    const Ref *r = &s->t->refs[s->step];
    Proc *p = &s->procs[r->proc];
    p->active = 1;
    p->refs++;

    int f = s->where[r->key];
    int fault = (f < 0);
    if (fault) {
        p->faults++;  s->faults++;
        f = get_frame(s);
        load_page(s, f, r->key);
    } else {
        p->hits++;    s->hits++;
        touch_page(s, f);
    }
    if (r->op == 'W')
        s->frames[f].dirty = 1;               /* a later read never clears it */

    /* Metrics after the reference, before any cleanup. */
    int old_ws = p->ws;
    window_push(p, r->key, fault);
    s->demand += p->ws - old_ws;
    long demand_pre = s->demand;
    double util = (double)s->used / s->nframes;
    s->util_sum += util;
    if (util > s->util_peak)
        s->util_peak = util;
    update_warnings(s);
    int frame_used = f;

    int any_warn = 0;                         /* system warning, sampled before cleanup */
    for (int i = 0; i < s->t->nprocs; i++)
        any_warn |= s->procs[i].warn;
    if (any_warn) {
        s->sys_warn_steps++;
        if (s->first_sys_warn == 0)
            s->first_sys_warn = s->step + 1;
    }
    for (int i = 0; i < s->t->nprocs; i++)
        s->procs[i].warn_steps += s->procs[i].warn;

    if (s->step == s->t->proc_last[r->proc])  /* this was the process's last reference */
        finish_process(s, r->proc);

    if (s->steplog)
        fprintf(s->steplog, "%ld,%d,%lld,%c,%c,%d,%d,%.2f,%ld,%ld,%d,%d\n",
                s->step + 1, r->pid, r->page, r->op, fault ? 'M' : 'H', frame_used,
                p->ws, (double)p->win_faults / p->win_len, demand_pre, s->demand,
                s->used, any_warn);
    s->step++;
}

void sim_run(Sim *s)
{
    while (s->step < s->t->n)
        sim_step(s);
}

void sim_free(Sim *s)
{
    free(s->frames);
    free(s->where);
    free(s->procs);
    s->frames = NULL;
    s->where = NULL;
    s->procs = NULL;
}

/* ---------- invariants ---------- */

#define FAIL(...) do { fprintf(stderr, "invariant broken at step %ld: ", s->step); \
                       fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); return -1; } while (0)

int sim_check(const Sim *s)
{
    const Trace *t = s->t;

    if (s->hits + s->faults != s->step)
        FAIL("hits + faults != references");
    if (s->used < 0 || s->used > s->nframes)
        FAIL("occupancy %d outside 0..%d", s->used, s->nframes);

    /* Every occupied frame has exactly one page, that page points back to it,
     * and no finished process still owns a frame. */
    int occupied = 0;
    for (int f = 0; f < s->nframes; f++) {
        int k = s->frames[f].key;
        if (k < 0)
            continue;
        occupied++;
        if (s->where[k] != f)
            FAIL("frame %d holds key %d, but the page table says frame %d", f, k, s->where[k]);
        if (s->procs[t->key_proc[k]].done)
            FAIL("finished pid %d still owns frame %d", t->proc_pid[t->key_proc[k]], f);
    }
    if (occupied != s->used)
        FAIL("used = %d but %d frames are occupied", s->used, occupied);

    /* No (pid, page) is resident twice: each resident key maps to a frame holding it. */
    for (int k = 0; k < t->npages; k++)
        if (s->where[k] >= 0 && s->frames[s->where[k]].key != k)
            FAIL("key %d maps to frame %d which holds another page", k, s->where[k]);

    for (int f = 0; f < s->free_hint && f < s->nframes; f++)
        if (s->frames[f].key < 0)
            FAIL("free frame %d is below free_hint %d", f, s->free_hint);

    if (uses_hand(s->policy)) {
        if (s->hand < 0 || (s->nframes > 0 && s->hand >= s->nframes))
            FAIL("clock hand %d out of range", s->hand);
    } else {                                  /* the queue holds exactly the resident pages */
        int len = 0;
        for (int f = s->head; f >= 0; f = s->frames[f].next) {
            if (s->frames[f].key < 0)
                FAIL("free frame %d is in the queue", f);
            if (++len > s->nframes)
                FAIL("queue has a cycle");
        }
        if (len != s->used)
            FAIL("queue length %d != used %d", len, s->used);
    }

    /* Per-process counters add up to the totals; demand matches the windows. */
    long refs = 0, hits = 0, faults = 0, wb = 0, fl = 0, demand = 0;
    for (int i = 0; i < t->nprocs; i++) {
        const Proc *p = &s->procs[i];
        if (p->hits + p->faults != p->refs)
            FAIL("pid %d: hits + faults != refs", t->proc_pid[i]);
        refs += p->refs; hits += p->hits; faults += p->faults;
        wb += p->writebacks; fl += p->flushes;
        if (p->active)
            demand += p->ws;
        if (p->done && p->warn)
            FAIL("finished pid %d still has a warning", t->proc_pid[i]);
    }
    if (refs != s->step || hits != s->hits || faults != s->faults ||
        wb != s->writebacks || fl != s->flushes)
        FAIL("per-process counts do not sum to the totals");
    if (demand != s->demand)
        FAIL("demand %ld != sum of active working sets %ld", s->demand, demand);
    return 0;
}
