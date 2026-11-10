/*
 * trace.c - CSV trace loader with strict validation.
 *
 * Loading happens in two passes:
 *   1. parse every line into a Ref (rejecting the file on the first error);
 *   2. give every process and every (pid, page) pair a small dense index,
 *      so the simulator can use plain arrays instead of hash lookups.
 */
#define _POSIX_C_SOURCE 200809L
#include "trace.h"

#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

int parse_int(const char *s, long long min, long long max, long long *out)
{
    const char *p = (*s == '-') ? s + 1 : s;
    if (*p == '\0')
        return -1;
    for (; *p; p++)                       /* digits only: rejects "2.5", " 3", "1e3" */
        if (*p < '0' || *p > '9')
            return -1;
    errno = 0;
    long long v = strtoll(s, NULL, 10);
    if (errno == ERANGE || v < min || v > max)
        return -1;
    *out = v;
    return 0;
}

/* Parses one data line "pid,page,op" into r. Returns NULL on success,
 * otherwise a short description of what is wrong. */
static const char *parse_line(char *line, Ref *r)
{
    if (*line == '\0')
        return "blank line";

    char *field[3];
    int nfields = 0;
    for (char *tok = line;; ) {
        char *comma = strchr(tok, ',');
        if (nfields < 3)
            field[nfields] = tok;
        nfields++;
        if (!comma)
            break;
        *comma = '\0';
        tok = comma + 1;
    }
    if (nfields != 3)
        return "expected exactly 3 fields: pid,page,op";

    long long v;
    if (parse_int(field[0], 1, INT_MAX, &v) != 0)
        return "pid must be a positive integer";
    r->pid = (int)v;
    if (parse_int(field[1], 0, LLONG_MAX, &v) != 0)
        return "page must be a non-negative integer";
    r->page = v;
    if (strcmp(field[2], "R") != 0 && strcmp(field[2], "W") != 0)
        return "op must be R or W";
    r->op = field[2][0];
    return NULL;
}

/* Removes a trailing "\n" or "\r\n". */
static void chomp(char *s)
{
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r'))
        s[--n] = '\0';
}

/* Pass 2: dense process indices and dense (pid, page) keys.
 * Pages are found through an open-addressing hash table. */
static int assign_ids(Trace *t)
{
    size_t cap = 16;
    while (cap < 2 * (size_t)t->n)
        cap *= 2;
    int *table = malloc(cap * sizeof *table);        /* stores key+1, 0 = empty */
    t->proc_pid  = malloc(sizeof(int));
    t->proc_last = malloc(sizeof(long));
    t->key_proc  = malloc((t->n + 1) * sizeof(int));
    t->key_page  = malloc((t->n + 1) * sizeof(long long));
    if (!table || !t->proc_pid || !t->proc_last || !t->key_proc || !t->key_page) {
        free(table);
        return -1;
    }
    memset(table, 0, cap * sizeof *table);

    for (long i = 0; i < t->n; i++) {
        Ref *r = &t->refs[i];

        /* Process index: few processes, so a linear search is enough. */
        int p = 0;
        while (p < t->nprocs && t->proc_pid[p] != r->pid)
            p++;
        if (p == t->nprocs) {
            int *pp  = realloc(t->proc_pid,  (p + 1) * sizeof(int));
            long *pl = realloc(t->proc_last, (p + 1) * sizeof(long));
            if (pp) t->proc_pid = pp;
            if (pl) t->proc_last = pl;
            if (!pp || !pl) { free(table); return -1; }
            t->proc_pid[p] = r->pid;
            t->nprocs++;
        }
        r->proc = p;
        t->proc_last[p] = i;

        /* Page key: hash the (proc, page) pair, then probe linearly. */
        uint64_t h = (uint64_t)r->page * 0x9E3779B97F4A7C15ull ^ (uint64_t)p * 0xC2B2AE3D27D4EB4Full;
        size_t slot = (h ^ (h >> 29)) & (cap - 1);
        while (table[slot] != 0) {
            int k = table[slot] - 1;
            if (t->key_proc[k] == p && t->key_page[k] == r->page)
                break;
            slot = (slot + 1) & (cap - 1);
        }
        if (table[slot] == 0) {                       /* first time we see this page */
            int k = t->npages++;
            t->key_proc[k] = p;
            t->key_page[k] = r->page;
            table[slot] = k + 1;
        }
        r->key = table[slot] - 1;
    }
    free(table);
    return 0;
}

int trace_read(FILE *f, const char *name, Trace *t)
{
    memset(t, 0, sizeof *t);
    char *line = NULL;
    size_t linecap = 0;
    long cap = 0, lineno = 1;

    if (getline(&line, &linecap, f) < 0) {
        fprintf(stderr, "%s: missing header line 'pid,page,op'\n", name);
        goto fail;
    }
    chomp(line);
    if (strcmp(line, "pid,page,op") != 0) {
        fprintf(stderr, "%s:1: header must be exactly 'pid,page,op', got '%s'\n", name, line);
        goto fail;
    }

    while (getline(&line, &linecap, f) >= 0) {
        lineno++;
        chomp(line);
        if (t->n == cap) {
            cap = cap ? 2 * cap : 1024;
            Ref *grown = realloc(t->refs, cap * sizeof(Ref));
            if (!grown) { fprintf(stderr, "%s: out of memory\n", name); goto fail; }
            t->refs = grown;
        }
        const char *err = parse_line(line, &t->refs[t->n]);
        if (err) {
            fprintf(stderr, "%s:%ld: %s\n", name, lineno, err);
            goto fail;
        }
        t->n++;
    }

    if (assign_ids(t) != 0) {
        fprintf(stderr, "%s: out of memory\n", name);
        goto fail;
    }
    free(line);
    return 0;

fail:
    free(line);
    trace_free(t);
    return -1;
}

int trace_load(const char *path, Trace *t)
{
    FILE *f = fopen(path, "r");
    if (!f) {
        perror(path);
        return -1;
    }
    int rc = trace_read(f, path, t);
    fclose(f);
    return rc;
}

void trace_free(Trace *t)
{
    free(t->refs);
    free(t->proc_pid);
    free(t->proc_last);
    free(t->key_proc);
    free(t->key_page);
    memset(t, 0, sizeof *t);
}
