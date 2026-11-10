/*
 * gen.c - vmgen: seeded workload generator. Writes a trace CSV to stdout.
 *
 *   vmgen zipf  -s SEED -n PROCS -r REFS -p PAGES -a ALPHA -w WRITEP
 *       Each process picks pages 0..PAGES-1 with Zipf(ALPHA) popularity
 *       (page 0 hottest), like the skewed block/web cache workloads in the
 *       HotOS'23 paper. Each reference is a write with probability WRITEP.
 *
 *   vmgen shift -s SEED -n PROCS -r REFS -p PAGES -a ALPHA -w WRITEP -k PHASES
 *       Same, but each process's whole working set moves to PAGES fresh pages
 *       PHASES times (sudden working-set changes, as in virtual memory).
 *
 *   vmgen phase [-p PAGES]
 *       The proposal's changing-locality trace for one process, all reads:
 *       80 refs over pages 0-1, 160 refs cycling pages 0..PAGES-1 (default 8),
 *       then 80 refs over pages 0-1.
 *
 * Interleaving is round-robin in ascending PID order; process i (0-based)
 * issues REFS - i*REFS/(2*PROCS) references, so processes finish at
 * different times and their freed frames get reused.
 * The same arguments always produce the same trace on every machine,
 * because the generator uses its own random number generator.
 */
#define _POSIX_C_SOURCE 200809L   /* getopt */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* splitmix64: tiny, fast and identical on every platform. */
static uint64_t rng_state;

static double rng_uniform(void)               /* in [0, 1) */
{
    uint64_t z = (rng_state += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    z ^= z >> 31;
    return (z >> 11) * 0x1.0p-53;
}

/* Samples a rank in 0..n-1 from the cumulative Zipf table `cdf`. */
static int zipf_sample(const double *cdf, int n)
{
    double u = rng_uniform() * cdf[n - 1];
    int lo = 0, hi = n - 1;
    while (lo < hi) {                         /* first rank with cdf > u */
        int mid = (lo + hi) / 2;
        if (cdf[mid] > u) hi = mid;
        else              lo = mid + 1;
    }
    return lo;
}

static void usage(void)
{
    fprintf(stderr,
        "usage: vmgen zipf  -s SEED -n PROCS -r REFS -p PAGES -a ALPHA -w WRITEP\n"
        "       vmgen shift -s SEED -n PROCS -r REFS -p PAGES -a ALPHA -w WRITEP -k PHASES\n"
        "       vmgen phase [-p PAGES]\n");
    exit(2);
}

static void phase_trace(int pages)
{
    printf("pid,page,op\n");
    for (int i = 0; i < 80; i++)  printf("1,%d,R\n", i % 2);
    for (int i = 0; i < 160; i++) printf("1,%d,R\n", i % pages);
    for (int i = 0; i < 80; i++)  printf("1,%d,R\n", i % 2);
}

int main(int argc, char **argv)
{
    if (argc < 2)
        usage();
    const char *mode = argv[1];
    long seed = 11, refs = 10000;
    int procs = 4, pages = 0, phases = 1, opt;   /* pages 0 = mode default */
    double alpha = 0.9, writep = 0.3;

    optind = 2;
    while ((opt = getopt(argc, argv, "s:n:r:p:a:w:k:")) != -1) {
        switch (opt) {
        case 's': seed   = atol(optarg); break;
        case 'n': procs  = atoi(optarg); break;
        case 'r': refs   = atol(optarg); break;
        case 'p': pages  = atoi(optarg); break;
        case 'a': alpha  = atof(optarg); break;
        case 'w': writep = atof(optarg); break;
        case 'k': phases = atoi(optarg); break;
        default: usage();
        }
    }
    if (pages == 0)
        pages = strcmp(mode, "phase") == 0 ? 8 : 100;
    if (procs < 1 || refs < 1 || pages < 1 || phases < 1 || alpha < 0 || writep < 0 || writep > 1) {
        fprintf(stderr, "vmgen: invalid parameter\n");
        return 2;
    }

    if (strcmp(mode, "phase") == 0) {
        phase_trace(pages);
        return 0;
    }
    if (strcmp(mode, "zipf") == 0)
        phases = 1;
    else if (strcmp(mode, "shift") != 0)
        usage();

    rng_state = (uint64_t)seed;
    double *cdf = malloc(pages * sizeof *cdf);
    long *count = calloc(procs, sizeof *count);  /* references issued so far */
    long *quota = malloc(procs * sizeof *quota); /* references each process issues */
    if (!cdf || !count || !quota)
        return 1;
    for (int i = 0; i < pages; i++)
        cdf[i] = (i ? cdf[i - 1] : 0) + 1.0 / pow(i + 1, alpha);
    for (int p = 0; p < procs; p++)
        quota[p] = refs - p * refs / (2 * procs);

    printf("pid,page,op\n");
    for (int active = procs; active > 0; ) {
        active = 0;
        for (int p = 0; p < procs; p++) {     /* round-robin, ascending PID */
            if (count[p] == quota[p])
                continue;
            long phase = count[p] * phases / quota[p];
            long page = phase * pages + zipf_sample(cdf, pages);
            char op = rng_uniform() < writep ? 'W' : 'R';
            printf("%d,%ld,%c\n", p + 1, page, op);
            count[p]++;
            active++;
        }
    }
    free(cdf);
    free(count);
    free(quota);
    return 0;
}
