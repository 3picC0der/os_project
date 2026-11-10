/*
 * trace.h - loading and validating memory-reference traces.
 *
 * A trace is a CSV file whose first line is exactly "pid,page,op".
 * Every following line is one reference, e.g. "1,0,R":
 *   pid  - positive integer (the process making the reference)
 *   page - non-negative virtual page number
 *   op   - 'R' (read) or 'W' (write), uppercase
 * Rows run in file order. A page is identified by the pair (pid, page),
 * so two processes never share a page.
 */
#ifndef TRACE_H
#define TRACE_H

#include <stdio.h>

typedef struct {
    int       pid;   /* process id as written in the file             */
    long long page;  /* virtual page number as written in the file    */
    char      op;    /* 'R' or 'W'                                     */
    int       proc;  /* dense process index: 0 .. nprocs-1             */
    int       key;   /* dense id of the (pid, page) pair: 0 .. npages-1 */
} Ref;

typedef struct {
    Ref       *refs;       /* all references, in file order              */
    long       n;          /* number of references                       */

    int        nprocs;     /* number of distinct processes               */
    int       *proc_pid;   /* proc index -> pid                          */
    long      *proc_last;  /* proc index -> index of its last reference  */

    int        npages;     /* number of distinct (pid, page) pairs       */
    int       *key_proc;   /* key -> owning proc index                   */
    long long *key_page;   /* key -> virtual page number                 */
} Trace;

/* Reads and validates a whole trace. On any error, prints the reason
 * (with the line number) to stderr, frees everything and returns -1.
 * Returns 0 on success. `name` is only used in error messages. */
int  trace_read(FILE *f, const char *name, Trace *t);
int  trace_load(const char *path, Trace *t);
void trace_free(Trace *t);

/* Strictly parses a decimal integer in [min, max]: digits only (an
 * optional leading '-'), no spaces, no fractions. Returns 0 on success. */
int  parse_int(const char *s, long long min, long long max, long long *out);

#endif
